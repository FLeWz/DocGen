"""Export pipeline: resolve pins into a self-contained spec (stored with the export record), then render.
PDF = the rendered DOCX converted by the Gotenberg container (LibreOffice)."""
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException

import httpx

from . import base_docs, config, docs, docx_export, files, vcs
from .db import SessionLocal, set_ctx
from .deps import Ctx
from .git import repo as git
from .git import content as C
from .models import BaseDocVersion, Document, Export, Folder, Organization, Role, Snippet, User
from .scoping import OrgRepo

_pool = ThreadPoolExecutor(2, thread_name_prefix="export")
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


def _heading_sections(blocks: list, key=lambda b: json.dumps(b, sort_keys=True)) -> dict:
    """Heading text -> fingerprint of the blocks up to the next heading ('' = the part before the first heading)."""
    out, cur = {"": []}, ""
    for b in blocks or []:
        if b.get("type") == "heading":
            cur = C._text(b).strip() or "(untitled)"
            out.setdefault(cur, [])
        out[cur].append(key(b))
    return {k: json.dumps(v) for k, v in out.items() if v}


def changed_headings(old: list | None, new: list | None) -> list[str]:
    """Headings whose section (the heading and the blocks under it) is new, removed or different; the part before
    the first heading is reported as ''. Sorted by position in `new`, then removed ones: deterministic."""
    a, b = _heading_sections(old), _heading_sections(new)
    return [k for k in [*b, *(k for k in a if k not in b)] if a.get(k) != b.get(k)]


def _snippet_sections(old: dict | None, new: dict | None) -> str:
    """'Title' or 'Title (Heading A, Heading B)' when the snippet's content differs between two versions, else ''."""
    if not new or (old and (old["content"], old["title"]) == (new["content"], new["title"])):
        return ""
    hs = [h for h in changed_headings(old and old["content"].get("content"), new["content"].get("content")) if h]
    return f"{new['title']} ({', '.join(hs)})" if hs else new["title"]


def _doc_sections(ctx: Ctx, old: dict | None, new: dict | None) -> str:
    """Blocks of a document version that differ from its predecessor, by id: heading text, snippet title, 'Paragraph', ..."""
    ob = {b["id"]: b for b in (old or {}).get("blocks") or []}
    nb = {b["id"]: b for b in (new or {}).get("blocks") or []}
    out = []
    for i in [*nb, *(k for k in ob if k not in nb)]:
        if ob.get(i) == nb.get(i):
            continue
        b = nb.get(i) or ob[i]
        t = b.get("type")
        if t == "heading":
            out.append(b.get("text") or "(heading)")
        elif t == "snippet_ref":
            sn = ctx.repo(Snippet).get(UUID(b["snippet_id"]))
            out.append(sn.title if sn else "snippet")
        else:
            out.append({"paragraph": "Paragraph", "toc": "Table of contents", "page_break": "Page break"}.get(t, t))
    for k, label in (("base_document", "Base document"), ("variables", "Variables"), ("links", "Linked documents")):
        if old and (old.get(k) or None) != (new.get(k) or None) and not (not old.get(k) and not new.get(k)):
            out.append(label)
    return ", ".join(dict.fromkeys(out))


def _base_headings(data: bytes | None) -> list:
    """Heading blocks of a base document's body, as {type, text...} blocks for `changed_headings`."""
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
        text = "".join(t.text or "" for t in p.iter(W + "t"))
        if st is not None and (st.get(W + "val") or "").lower().startswith(("heading", "title")) and text.strip():
            out.append({"type": "heading", "text": text.strip()})
        elif text.strip() or p.find(f".//{W}drawing") is not None:
            out.append({"type": "paragraph", "text": text.strip()})
    return out


def revision_rows(ctx: Ctx, d: Document, blocks: list[dict], base: dict | None = None) -> list[dict]:
    """Auto revision history, oldest first: commits of the document, of each pinned snippet (up to its pin) and of
    the pinned base document (edits/versions after the first, up to its pin). Row: version (tag or short sha), date,
    author, message + commit (the subject) and sections = what that commit changed (blocks/headings of the document,
    snippet titles with changed headings, changed headings of the base document)."""
    rows: dict[str, dict] = {}

    def add(repo, commits, sections, skip_oldest=False):
        tm = vcs.tag_map(repo)
        for i, c in enumerate(commits):  # newest first: the predecessor is the next entry
            if skip_oldest and i == len(commits) - 1:
                continue
            sec = sections(c, commits[i + 1] if i + 1 < len(commits) else None)
            if c["sha"] in rows:
                r = rows[c["sha"]]
                r["sections"] = ", ".join(dict.fromkeys(x for x in [r["sections"], sec] if x))
                continue
            c = vcs.commit_out(c, tm)
            subject = c["message"].split("\n")[0]
            rows[c["sha"]] = dict(version=(c["tags"] or [c["sha"][:7]])[0], date=c["date"][:10], author=c["author_name"], message=subject,
                                  sections=sec, commit=subject, _k=(c["date"], c["sha"]))

    repo = vcs.repo_of(ctx, d.folder_id)
    add(repo, git.log(repo, d.id, key="Document-Id"),
        lambda c, p: _doc_sections(ctx, p and docs.version_at(ctx, d, p["sha"]), docs.version_at(ctx, d, c["sha"])))
    for b in blocks:
        s = b["type"] == "snippet_ref" and ctx.repo(Snippet).get(UUID(b["snippet_id"]))
        sha = s and docs.pin_sha(ctx, b, s)
        if sha:
            srepo = vcs.repo_of(ctx, s.folder_id)
            add(srepo, git.log(srepo, s.id, rev=sha),
                lambda c, p, s=s, srepo=srepo: _snippet_sections(p and C.version_at(srepo, p["sha"], s.id), C.version_at(srepo, c["sha"], s.id)))
    bid = base and UUID(base["id"])
    brepo = git.base_repo_path(ctx.org_id)
    if bid and base.get("sha") and brepo.exists():
        path = f"base/{bid}.docx"
        commits = git.log(brepo, bid, key="Base-Document-Id", rev=base["sha"])
        add(brepo, commits, lambda c, p: ", ".join(h or "(front matter)" for h in changed_headings(
            _base_headings(p and git.show(brepo, p["sha"], path)), _base_headings(git.show(brepo, c["sha"], path)))),
            skip_oldest=True)  # the upload of version 1 is not a revision of the document
    return [{k: v for k, v in r.items() if k != "_k"} for r in sorted(rows.values(), key=lambda r: r["_k"])]


def build_spec(ctx: Ctx, d: Document) -> dict:
    """Everything the renderer needs. Tags and `latest` are frozen to commit shas here."""
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

        document=dict(id=str(d.id), slug=w["slug"], head_sha=d.head_sha, draft=docs.draft_of(ctx, d) is not None),
        title=w["title"], base=br and dict(id=br["id"], sha=br["version"]), blocks=blocks,
        variables=w["variables"], links=w["links"],
        revision_history=w.get("revision_history") if w.get("revision_history") is not None else revision_rows(ctx, d, w["blocks"], br and dict(id=br["id"], sha=br["version"])),
        built_ins={"doc.title": w["title"], "doc.version": (d.head_sha or "draft")[:7],
                   "doc.date": datetime.now(timezone.utc).date().isoformat(), "org.name": org.name,
                   "author": ctx.user.name})


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


def create(ctx: Ctx, d: Document, fmt: str) -> Export:
    spec = build_spec(ctx, d)
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


def submit(org_id, user_id, export_id) -> None:
    _pool.submit(_run, org_id, user_id, export_id)


def _run(org_id, user_id, export_id) -> None:
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
        except Exception as x:  # job boundary: record, never crash the worker
            e.status, e.error = "error", re.sub(r"\s+", " ", str(x))[:500] or type(x).__name__
        e.finished_at = datetime.now(timezone.utc)
        db.commit()


def expire_stale(ctx: Ctx, e: Export) -> None:
    """The queue lives in this process: a restart mid-job leaves a row that would never finish."""
    if e.status in ("queued", "running") and datetime.now(timezone.utc) - e.created_at > STALE:
        e.status, e.error = "error", "export interrupted"
        ctx.db.commit()
