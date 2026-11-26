"""Export pipeline: resolve pins into a self-contained spec (stored with the export record), then render.
PDF = the rendered DOCX converted by the Gotenberg container (LibreOffice)."""
import hashlib
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException

import httpx

from .dates import dmy
from . import base_docs, config, docs, docx_export, files, sections as S, vcs
from .db import SessionLocal, set_ctx
from .deps import Ctx
from .git import repo as git
from .git import content as C
from .models import BaseDocVersion, Document, Export, Folder, Organization, Role, Snippet, User
from .scoping import OrgRepo

_pool = ThreadPoolExecutor(2, thread_name_prefix="export")
EXPORTER_VERSION = "13.1"  # bump when DOCX/PDF output changes for the same spec: cached renders of older versions are never hit
CACHE_KEEP = 10  # newest cached renders kept per document
_inflight: dict[str, UUID] = {}  # cache key -> running export: concurrent identical requests share one render (single process)
_lock = threading.Lock()
STALE = timedelta(minutes=10)


BUILTIN_ROOTS = {"doc", "org", "author", "revision_history", "linked_documents"}


def used_variables(node, out: dict) -> None:
    """name -> {"list": bool, "required": bool}; chips with a default don't require a value, row.* is bound by its table."""
    if not isinstance(node, dict):
        return
    a = node.get("attrs") or {}
    t = node.get("type")
    if t == "table" and a.get("dynamicList") and a["dynamicList"] not in BUILTIN_ROOTS:  # revision_history: filled by the exporter
        o = out.setdefault(a["dynamicList"], dict(list=True, required=True))
        o["list"], o["required"] = True, o["required"] and not isinstance(a.get("dynamicRows"), list)  # its own default rows (made from existing rows)
    if t == "variable":
        n = a.get("name") or ""
        if not n.startswith("row.") and n.split(".")[0] not in BUILTIN_ROOTS:
            o = out.setdefault(n, dict(list=False, required=False))
            o["required"] |= a.get("default") in (None, "")
    for c in node.get("content") or []:
        used_variables(c, out)


def missing_variables(variables: dict, used: dict) -> list[dict]:
    """Declared variables and used chips lacking both a value and a default (declared => required)."""
    vals, out = variables.get("values", {}), {}
    decls = {d["name"]: d for d in variables.get("declarations", [])}
    for n in sorted({*decls, *(k for k, u in used.items() if u["required"])}):
        d = decls.get(n)
        if vals.get(n) in (None, "") and (not d or d.get("default") in (None, "")):
            out[n] = dict(name=n, type=d["type"] if d else "list" if used.get(n, {}).get("list") else "text")
    return list(out.values())


def _flat(ctx: Ctx, title: str, blocks: list[dict], override: dict | None = None):
    """(nodes, titles, starts): the top-level nodes of the BUILT document in order (snippet pins resolved, builder headings
    as heading nodes). `override` {snippet id: (title, nodes)} swaps one snippet's version; starts[snippet id] = its offset."""
    nodes, titles, starts = [], [], {}
    for b in blocks or []:
        t, got, ttl = b["type"], [], title
        if t == "snippet_ref":
            sn = ctx.repo(Snippet).get(UUID(b["snippet_id"]))
            if not sn:
                continue
            if override and sn.id in override:
                ttl, got = override[sn.id]
            else:
                sha = docs.pin_sha(ctx, b, sn)
                v = sha and C.version_at(vcs.repo_of(ctx, sn.folder_id), sha, sn.id)
                if not v:
                    continue
                ttl, got = v["title"], v["content"].get("content") or []
            starts.setdefault(sn.id, len(nodes))
        elif t == "paragraph":
            got = (b.get("content") or {}).get("content") or []
        elif t == "heading":
            got = [{"type": "heading", "attrs": {"level": b["level"], "numbered": b.get("numbered"), "styleId": b.get("styleId"), "_blk": True},
                    "content": [{"type": "text", "text": b["text"]}]}]
        nodes += got
        titles += [ttl] * len(got)
    return nodes, titles, starts


def _numbering(ctx: Ctx, base: dict | None) -> S.Numbering:
    """Heading numbering of the pinned base document version (the bundled default when none)."""
    data = None
    if base and base.get("sha"):
        brepo = git.base_repo_path(ctx.org_id)
        data = brepo.exists() and git.show(brepo, base["sha"], f"base/{base['id']}.docx")
    return S.Numbering(data or base_docs.default_docx())


def _base_nodes(data: bytes | None) -> list:
    """Paragraphs of a base document's body as nodes: headings (style Heading N / Title, direct numPr kept), the rest as text."""
    if not data:
        return []
    import io, zipfile
    from lxml import etree
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = etree.fromstring(z.read("word/document.xml"), etree.XMLParser(resolve_entities=False, no_network=True))
    out = []
    for p in root.iter(W + "p"):
        st = p.find(f"{W}pPr/{W}pStyle")
        sid = st.get(W + "val") if st is not None else None
        text = "".join(t.text or "" for t in p.iter(W + "t")).strip()
        if sid and sid.lower().startswith(("heading", "title")) and text:
            m = re.search(r"(\d)$", sid)
            np = p.find(f"{W}pPr/{W}numPr/{W}numId")
            out.append({"type": "heading", "attrs": {"level": int(m[1]) if m else 1, "styleId": sid,
                                                     **({"_numpr": np.get(W + "val")} if np is not None else {})},
                        "content": [{"type": "text", "text": text}]})
        elif text or p.find(f".//{W}drawing") is not None:
            out.append({"type": "paragraph", "content": [{"type": "text", "text": text}]})
    return out


def _doc_sec(ctx: Ctx, d: Document, old: dict | None, new: dict | None) -> str:
    """Structural changes between two committed versions of a document, numbered over each built document."""
    if not new:
        return ""
    nn, nt, _ = _flat(ctx, new["title"], new.get("blocks"))
    on, ot, _ = _flat(ctx, old["title"], old.get("blocks")) if old else ([], [], {})
    br = new.get("base_document")
    return S.diff_nodes(on, nn, ot, nt, _numbering(ctx, br and dict(id=br["id"], sha=br["version"])))


def _snippet_sec(ctx: Ctx, d: Document, blocks: list[dict], s: Snippet, num: S.Numbering, old: dict | None, new: dict | None) -> str:
    """Changes of one snippet commit, located by where the snippet sits in the document built from `blocks`."""
    if not new or (old and (old["content"], old["title"]) == (new["content"], new["title"])):
        return ""

    def seg(v: dict | None):
        nodes = (v["content"].get("content") if v else None) or []
        flat, titles, starts = _flat(ctx, d.title, blocks, {s.id: (v["title"] if v else s.title, nodes)})
        off = starts.get(s.id, 0)
        return nodes, S.anchors(flat, titles, num)[off:off + len(nodes)], off
    nn, na, off = seg(new)
    on, oa, _ = seg(old)
    return S.changes(on, nn, na, oa, off)


def revision_rows(ctx: Ctx, d: Document, blocks: list[dict], base: dict | None = None, rev: str | None = None) -> list[dict]:
    """Auto revision history, oldest first: commits of the document, of each pinned snippet (up to its pin) and of
    the pinned base document (edits/versions after the first, up to its pin). Row: version (tag or short sha), date,
    author, message + commit (the subject) and sections = what that commit changed (blocks/headings of the document,
    snippet titles with changed headings, changed headings of the base document)."""
    rows: dict[str, dict] = {}

    def add(repo, scope, commits, sections, skip_oldest=False):
        tm, nm = vcs.tag_map(repo), vcs.names(ctx, scope)
        for i, c in enumerate(commits):  # newest first: the predecessor is the next entry
            if skip_oldest and i == len(commits) - 1:
                continue
            sec = sections(c, commits[i + 1] if i + 1 < len(commits) else None)
            if c["sha"] in rows:
                r = rows[c["sha"]]
                r["sections"] = ", ".join(dict.fromkeys(x for v in (r["sections"], sec) for x in v.split(", ") if x))
                continue
            c = vcs.commit_out(c, tm, nm)
            subject = c["message"].split("\n")[0]
            rows[c["sha"]] = dict(version=(c["tags"] or [c["sha"][:7]])[0], date=dmy(c["date"][:10]), author=", ".join(dict.fromkeys([c["author_name"], *(x["name"] for x in c["co_authors"])])), message=subject,
                                  sections=sec, commit=subject, status="Tagged" if c["tags"] else "Committed", _k=(c["date"], -len(rows)))  # same-second commits: git log is newest first

    repo = vcs.repo_of(ctx, d.folder_id)
    add(repo, d.folder_id, git.log(repo, d.id, key="Document-Id", rev=rev),  # rev: only commits up to a historic version
        lambda c, p: _doc_sec(ctx, d, p and docs.version_at(ctx, d, p["sha"]), docs.version_at(ctx, d, c["sha"])))
    num = _numbering(ctx, base)
    for b in blocks:
        s = b["type"] == "snippet_ref" and ctx.repo(Snippet).get(UUID(b["snippet_id"]))
        sha = s and docs.pin_sha(ctx, b, s)
        if sha:
            srepo = vcs.repo_of(ctx, s.folder_id)
            add(srepo, s.folder_id, git.log(srepo, s.id, rev=sha),
                lambda c, p, s=s, srepo=srepo: _snippet_sec(ctx, d, blocks, s, num, p and C.version_at(srepo, p["sha"], s.id),
                                                            C.version_at(srepo, c["sha"], s.id)))
    bid = base and UUID(base["id"])
    brepo = git.base_repo_path(ctx.org_id)
    if bid and base.get("sha") and brepo.exists():
        path = f"base/{bid}.docx"
        commits = git.log(brepo, bid, key="Base-Document-Id", rev=base["sha"])
        def base_sec(c, p):
            new = git.show(brepo, c["sha"], path)
            old = _base_nodes(p and git.show(brepo, p["sha"], path))
            nn = _base_nodes(new)
            return S.diff_nodes(old, nn, ["(front matter)"] * len(old), ["(front matter)"] * len(nn), S.Numbering(new))
        add(brepo, vcs.BASE_SCOPE, commits, base_sec,
            skip_oldest=True)  # the upload of version 1 is not a revision of the document
    return [{k: v for k, v in r.items() if k != "_k"} for r in sorted(rows.values(), key=lambda r: r["_k"])]


def build_spec(ctx: Ctx, d: Document, at: str | None = None) -> dict:
    """Everything the renderer needs. Tags and `latest` are frozen to commit shas here. `at`: the document as committed
    there (its own pins, variables, base version; doc.version = tag or short sha, doc.date/author = that commit's)."""
    c = None
    if at:
        repo = vcs.repo_of(ctx, d.folder_id)
        try:
            log = git.log(repo, d.id, key="Document-Id", rev=at)
        except git.NotFound:
            raise HTTPException(404, "version not found")
        w = log and log[0]["sha"] == git.resolve(repo, at) and docs.version_at(ctx, d, log[0]["sha"])
        if not w:
            raise HTTPException(404, "version not found")
        c = vcs.commit_out(log[0], vcs.tag_map(repo), vcs.names(ctx, d.folder_id))
    else:
        w = docs.working(ctx, d)
    blocks = []
    for b in w["blocks"]:
        b = dict(b)
        if b["type"] == "snippet_ref":
            f, s = ctx.repo(Folder).get(UUID(b["folder_id"])), ctx.repo(Snippet).get(UUID(b["snippet_id"]))
            sha = f and s and docs.pin_sha(ctx, b, s)
            if not sha:
                raise HTTPException(422, f"block {b['id']}: referenced snippet version is not available")
            b["pin"] = dict(folder_id=b["folder_id"], snippet_id=b["snippet_id"], sha=sha)
        blocks.append(b)
    br = w["base_document"]
    org = ctx.db.get(Organization, ctx.org_id)
    bv = br and ctx.repo(BaseDocVersion).first(BaseDocVersion.base_document_id == UUID(br["id"]), BaseDocVersion.sha == br["version"])
    return dict(base_variables=bv.variables if bv else [],  # frozen: defaults edited later must not change a stored spec

        document=dict(id=str(d.id), slug=w["slug"], head_sha=c["sha"] if c else d.head_sha, draft=not c and docs.draft_of(ctx, d) is not None),
        title=w["title"], base=br and dict(id=br["id"], sha=br["version"]), blocks=blocks,
        variables=w["variables"], links=w["links"],
        revision_history=w.get("revision_history") if w.get("revision_history") is not None else revision_rows(ctx, d, w["blocks"], br and dict(id=br["id"], sha=br["version"]), c and c["sha"]),
        built_ins={"doc.title": w["title"], "doc.version": c and (c["tags"] or [c["sha"][:7]])[0] or (d.head_sha or "draft")[:7],
                   "doc.date": c["date"][:10] if c else datetime.now(timezone.utc).date().isoformat(), "org.name": org.name,
                   "author": c["author_name"] if c else ctx.user.name})


def pins_of(spec: dict) -> dict:
    return dict(base=spec["base"], document=spec["document"],
                snippets=[dict(block_id=b["id"], **b["pin"], pin=b["version"]) for b in spec["blocks"] if "pin" in b])


def spec_hash(spec: dict) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True, default=str).encode()).hexdigest()


def cached(ctx: Ctx, d: Document, fmt: str) -> Export | None:
    """A finished export of this document whose frozen spec (pins, variables, base, blocks, built-ins) equals the current one: its file can be
    served again instead of rendering the same thing. Looks at the latest 20 exports only."""
    h = spec_hash(build_spec(ctx, d))
    for e in sorted(ctx.repo(Export).list(Export.document_id == d.id, Export.format == fmt, Export.status == "done"),
                    key=lambda e: e.created_at, reverse=True)[:20]:
        if spec_hash(e.spec) == h and files.export_path(ctx.org_id, e.id, fmt).exists():
            return e
    return None


def cache_key(spec: dict, fmt: str) -> str:
    """Render-input hash. The spec already holds everything the output depends on: pinned shas (a `latest` pin is frozen to
    the snippet's HEAD sha, so a new commit there is a miss), variables, base version, and the revision rows with their
    tags (so adding/removing/renaming a tag on a commit of the history is a miss). Plus format and exporter version."""
    return hashlib.sha256(f"{EXPORTER_VERSION}|{fmt}|{spec_hash(spec)}".encode()).hexdigest()


def cache_prune(org_id, doc_id) -> None:
    fs = sorted(files.cache_dir(org_id, doc_id).glob("*.*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in fs[CACHE_KEEP:]:
        p.unlink(missing_ok=True)


def start_version(ctx: Ctx, d: Document, sha: str, fmt: str) -> dict:
    """Export of a committed version: served from the cache when the render inputs are identical, else rendered once even
    when requested concurrently. Every call is recorded in the exports table."""
    spec = build_spec(ctx, d, sha)
    key = cache_key(spec, fmt)
    path = files.cache_path(ctx.org_id, d.id, spec["document"]["head_sha"], key, fmt)
    with _lock:
        if (eid := _inflight.get(key)) and (e := ctx.repo(Export).get(eid)) and e.status in ("queued", "running"):
            return dict(export_id=e.id, status=e.status, cached=False)
        e = create(ctx, d, fmt, spec)
        hit = path.exists()
        if hit:
            e.status, e.sha256, e.finished_at = "done", hashlib.sha256(path.read_bytes()).hexdigest(), datetime.now(timezone.utc)
            dst = files.export_path(ctx.org_id, e.id, fmt)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(path.read_bytes())
            os.utime(path)  # keeps recently used renders when pruning
        else:
            _inflight[key] = e.id
        ctx.db.commit()
    if not hit:
        submit(ctx.org_id, ctx.user.id, e.id, (key, path))
    return dict(export_id=e.id, status=e.status, cached=hit)


def create(ctx: Ctx, d: Document, fmt: str, spec: dict | None = None) -> Export:
    spec = spec or build_spec(ctx, d)
    used: dict = {}
    for b in spec["blocks"]:
        v = "pin" in b and C.version_at(git.repo_path(ctx.org_id, b["pin"]["folder_id"]), b["pin"]["sha"], b["pin"]["snippet_id"])
        used_variables(v and v["content"], used)
        if b["type"] == "paragraph":
            used_variables(b["content"], used)
    v = spec["variables"]
    own = {d["name"] for d in v["declarations"]}
    merged = dict(v, declarations=[*(d for d in spec["base_variables"] if d["name"] not in own), *v["declarations"]])
    if miss := missing_variables(merged, used):
        raise HTTPException(422, dict(error="missing_variables", variables=miss,
                                      message="Missing required variables: " + ", ".join(m["name"] for m in miss)))
    return ctx.repo(Export).add(document_id=d.id, user_id=ctx.user.id, format=fmt, status="queued",
                                pins=pins_of(spec), variables=spec["variables"], spec=spec, warnings=[])


def render_docx(org_id, spec: dict) -> tuple[bytes, list]:
    if spec["base"]:
        base = git.show(git.base_repo_path(org_id), spec["base"]["sha"], f"base/{spec['base']['id']}.docx")
        if base is None:
            raise ValueError("pinned base document version not found")
    else:
        base = base_docs.default_docx()
    cache: dict = {}

    def content(pin):
        k = (pin["folder_id"], pin["snippet_id"], pin["sha"])
        if k not in cache:
            v = C.version_at(git.repo_path(org_id, pin["folder_id"]), pin["sha"], pin["snippet_id"])
            cache[k] = v and (v["content"], v["slug"])
        return cache[k]

    def asset(pin, slug, image_id):
        try:
            iid = UUID(str(image_id))
        except ValueError:
            return None
        repo = git.repo_path(org_id, pin["folder_id"])
        return next((b for ext in ("png", "jpg", "gif", "webp")
                     if (b := git.show(repo, pin["sha"], f"snippets/{slug}/assets/{iid}.{ext}"))), None)

    return docx_export.build(spec, base, content, asset)


def render_pdf(org_id, spec: dict) -> tuple[bytes, list]:
    """DOCX -> Gotenberg/LibreOffice. updateIndexes makes LibreOffice refresh the TOC and page-number fields."""
    docx, warnings = render_docx(org_id, spec)
    return docx_to_pdf(docx), warnings


def docx_to_pdf(docx: bytes) -> bytes:
    try:
        with httpx.stream("POST", f"{config.PDF_URL}/forms/libreoffice/convert", timeout=config.PDF_TIMEOUT_S,
                          files={"files": ("document.docx", docx, MIME["docx"])}, data={"updateIndexes": "true"}) as r:
            if r.status_code != 200:
                raise ValueError(f"PDF conversion failed (HTTP {r.status_code}): {r.read()[:200].decode(errors='replace')}")
            out = bytearray()
            for chunk in r.iter_bytes():
                out += chunk
                if len(out) > config.PDF_MAX_BYTES:
                    raise ValueError(f"PDF larger than the {config.PDF_MAX_BYTES // 2**20} MiB limit")
    except httpx.TimeoutException:
        raise ValueError(f"PDF conversion timed out after {config.PDF_TIMEOUT_S:g}s")
    except httpx.HTTPError as e:
        raise ValueError(f"PDF service unavailable ({type(e).__name__})")
    if not out.startswith(b"%PDF"):
        raise ValueError("PDF service returned something that is not a PDF")
    return bytes(out)


RENDERERS = {"docx": render_docx, "pdf": render_pdf}
MIME = {"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "pdf": "application/pdf"}


def submit(org_id, user_id, export_id, cache: tuple | None = None) -> None:
    _pool.submit(_run, org_id, user_id, export_id, cache)


def _run(org_id, user_id, export_id, cache: tuple | None = None) -> None:
    with SessionLocal() as db:
        set_ctx(db, org_id=org_id, user_id=user_id)
        repo = OrgRepo(db, Export, org_id)
        e = repo.get(export_id)
        e.status = "running"
        db.commit()
        try:
            data, warnings = RENDERERS[e.format](org_id, e.spec)
            p = files.export_path(org_id, e.id, e.format)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            e.sha256, e.warnings, e.status = hashlib.sha256(data).hexdigest(), warnings, "done"
            if cache:  # atomic: a reader never sees half a file
                cache[1].parent.mkdir(parents=True, exist_ok=True)
                tmp = cache[1].with_suffix(".tmp")
                tmp.write_bytes(data)
                os.replace(tmp, cache[1])
                cache_prune(org_id, e.document_id)
        except Exception as x:  # job boundary: record, never crash the worker
            e.status, e.error = "error", re.sub(r"\s+", " ", str(x))[:500] or type(x).__name__
        e.finished_at = datetime.now(timezone.utc)
        db.commit()
        if cache:
            with _lock:
                _inflight.pop(cache[0], None)


def expire_stale(ctx: Ctx, e: Export) -> None:
    """The queue lives in this process: a restart mid-job leaves a row that would never finish."""
    if e.status in ("queued", "running") and datetime.now(timezone.utc) - e.created_at > STALE:
        e.status, e.error = "error", "export interrupted"
        ctx.db.commit()
