import copy
import difflib
import hashlib
import io
import re
import zipfile
from uuid import UUID

from lxml import etree
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import text
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile

from datetime import datetime, timezone

from .. import base_docs, docx_export, docx_hf, docx_import as D, docx_vars, locks, vcs
from ..deps import Ctx, require
from ..files import image_path, import_path
from ..git import repo as git
from ..models import BaseDocument, BaseDocVersion, Draft, Image, Import, Organization, Role, Snippet
from ..ooxml import collect_parts, neutralize_ole, opaque_element, rel_ids, rel_map
from ..schemas import BaseCommitIn, BaseRestoreIn, VarPicks, ImportBaseIn, ConflictsIn, DraftIn, ImportPut, ImportSnippetOut, SelectionIn, SplitIn, UpdateApplyIn, UpdatePlanIn
from . import images
from .base_documents import _get as get_base, _head_docx, _read, _store, _versions, add_base, named

router = APIRouter(tags=["imports"])
V = Depends(require(Role.viewer))
E = Depends(require(Role.editor))
A = Depends(require(Role.admin))
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MAX_SECTIONS = 1000


def _get(ctx: Ctx, id: UUID) -> Import:
    imp = ctx.repo(Import).get(id)
    if not imp or (imp.base_document_id and imp.created_by != ctx.user.id):  # base-edit drafts are per user, like snippet drafts
        raise HTTPException(404, "import not found")
    return imp


def _orig(ctx: Ctx, imp: Import) -> bytes:
    return import_path(ctx.org_id, imp.id).read_bytes()


def _meta(i: Import) -> dict:
    return dict(id=i.id, title=i.title, filename=i.filename, created_at=i.created_at, updated_at=i.updated_at,
                base_document_id=i.base_document_id)


@router.post("/import/docx", status_code=201)
def import_docx(file: UploadFile = File(), title: str | None = Form(default=None, max_length=200), ctx: Ctx = E):
    data = _read(file)
    try:  # same gate as base documents: zip structure, bombs, XXE, macros
        base_docs.validate(data)
        data, ole_warn = neutralize_ole(data)

        def store(b: bytes):
            try:
                return str(images.save(ctx, b).id)
            except HTTPException:
                return None
        content, warnings = D.parse(data, store)
        warnings = ole_warn + warnings
    except base_docs.BadDocx as e:
        raise HTTPException(422, str(e))
    if len(str(content)) > vcs.MAX_DOC:
        raise HTTPException(413, "document too large to edit")
    fn = re.sub(r"[^A-Za-z0-9._ -]", "_", file.filename or "import.docx")[:200]
    imp = ctx.repo(Import).add(title=(title or fn.rsplit(".", 1)[0] or "Imported document")[:200], filename=fn,
                               content=content, warnings=warnings, created_by=ctx.user.id)
    p = import_path(ctx.org_id, imp.id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    ctx.db.commit()
    return dict(**_meta(imp), warnings=warnings)


@router.get("/imports")
def list_imports(ctx: Ctx = V):
    return [_meta(i) for i in sorted(ctx.repo(Import).list(Import.base_document_id.is_(None)), key=lambda i: i.created_at, reverse=True)]


@router.get("/imports/{import_id}")
def get_import(import_id: UUID, ctx: Ctx = V):
    i = _get(ctx, import_id)
    return dict(**_meta(i), content=i.content, warnings=i.warnings)


@router.delete("/imports/{import_id}", status_code=204)
def delete_import(import_id: UUID, ctx: Ctx = E):
    """Row + original file; images it created stay until the cleanup endpoint finds them unreferenced."""
    i = _get(ctx, import_id)
    ctx.repo(Import).delete(i)
    ctx.db.commit()
    import_path(ctx.org_id, i.id).unlink(missing_ok=True)


@router.get("/imports/{import_id}/headers-footers")
def headers_footers(import_id: UUID, ctx: Ctx = E):
    """Every header/footer part of the package as editable content (the edited version where the import has one)."""
    i = _get(ctx, import_id)
    data, edited, out = _orig(ctx, i), (i.content or {}).get("hf") or {}, []
    for path in docx_hf.part_paths(data):
        warn: list = []
        if path in edited:
            doc = edited[path]
        else:
            doc, warn = docx_hf.part_doc(data, path, lambda b: _store_image(ctx, b))
        out.append(dict(path=path, kind="header" if "header" in path else "footer", doc=doc, edited=path in edited, warnings=warn))
    ctx.db.commit()  # images of the parts were stored in the org image store
    return dict(parts=out)


def _store_image(ctx: Ctx, b: bytes):
    try:
        return str(images.save(ctx, b).id)
    except HTTPException:
        return None


@router.put("/imports/{import_id}")
def put_import(import_id: UUID, body: ImportPut, ctx: Ctx = E):
    i = _get(ctx, import_id)
    if len(str(body.content)) > vcs.MAX_DOC:
        raise HTTPException(413, "document too large")
    try:
        D.check_content(body.content, rel_ids(_orig(ctx, i)))
        docx_hf.check(body.content.get("hf"), _orig(ctx, i))
    except ValueError as e:
        raise HTTPException(422, f"invalid opaque block: {e}")
    i.content = body.content
    decls = docx_vars.table_decls(body.content)  # dynamic tables made from existing rows: the list variable with its default rows
    if decls:
        i.variables = [*(d for d in (i.variables or []) if d["name"] not in {x["name"] for x in decls}), *decls]
    if body.title:
        i.title = body.title
    ctx.db.commit()
    return dict(**_meta(i), content=i.content, warnings=i.warnings)


def _asset(ctx: Ctx):
    def asset(_c, iid):
        img = ctx.repo(Image).get(UUID(str(iid))) if re.fullmatch(r"[0-9a-f-]{36}", str(iid)) else None
        return img and image_path(ctx.org_id, img.id, img.ext).read_bytes()
    return asset


def import_context(ctx: Ctx, i: Import) -> dict:
    """jinja context for `{{ }}` texts in an import's own export: built-ins and the declared defaults."""
    spec = dict(variables=dict(declarations=i.variables or [], values={}), built_ins={
        "doc.title": i.title, "doc.version": "draft", "org.name": ctx.db.get(Organization, ctx.org_id).name,
        "author": ctx.user.name, "doc.date": datetime.now(timezone.utc).date().isoformat()})
    return docx_export.context(spec)


@router.get("/imports/{import_id}/export")
def export_import(import_id: UUID, ctx: Ctx = V):
    i = _get(ctx, import_id)
    try:
        data, warnings = D.export(_orig(ctx, i), i.content, _asset(ctx), import_context(ctx, i))
    except Exception as e:  # python-docx on content that the editor produced but Word structure rejects
        raise HTTPException(422, f"export failed: {type(e).__name__}: {e}")
    name = re.sub(r"[^A-Za-z0-9._-]", "_", i.title) or "document"
    return Response(data, media_type=DOCX, headers={
        "Content-Disposition": f'attachment; filename="{name}.docx"', "X-Content-Type-Options": "nosniff",
        "X-Export-Warnings": str(len(warnings))})


def _headings(content: dict) -> list[dict]:
    """Every heading (top-level block): its block index, level, title, and the blocks/characters up to the next heading."""
    blocks = content.get("content") or []
    hs = [k for k, b in enumerate(blocks) if b.get("type") == "heading"]
    out = []
    for k, e in zip(hs, hs[1:] + [len(blocks)]):
        out.append(dict(index=k, level=(blocks[k].get("attrs") or {}).get("level", 1), title=(D.block_text(blocks[k]).strip() or "Untitled section")[:200],
                        blocks=e - k, chars=sum(len(D.block_text(b)) for b in blocks[k:e])))
    return out


def _sections(content: dict, level: int | None = None, cuts: list[int] | None = None, preface: bool = True, titles: dict | None = None) -> list[dict]:
    """Top-level blocks cut before every Heading <= level (or before the explicit `cuts`: a snippet runs to the next
    cut, so unselected headings merge into the previous one). Blocks before the first cut are 'Front matter' if `preface`."""
    blocks = content.get("content") or []
    if cuts is None:
        cuts = [k for k, b in enumerate(blocks) if b.get("type") == "heading" and (b.get("attrs") or {}).get("level", 1) <= level]
    bounds = ([0] if preface and (not cuts or cuts[0] > 0) else []) + cuts + [len(blocks)]
    out = []
    for s, e in zip(bounds, bounds[1:]):
        head = blocks[s] if s in cuts else None
        title = (D.block_text(head).strip() if head else "Front matter") or "Untitled section"
        if head is None and not any(D.block_text(b).strip() or b.get("type") != "paragraph" for b in blocks[s:e]):
            continue  # nothing but empty paragraphs before the first heading
        title = ((titles or {}).get(s if head else -1) or "").strip() or title
        out.append(dict(key=s if head else -1, title=title[:200], start=s, end=e, blocks=e - s, level=(head or {}).get("attrs", {}).get("level")))
    return out


@router.get("/imports/{import_id}/headings")
def headings(import_id: UUID, ctx: Ctx = V):
    return _headings(_get(ctx, import_id).content)


@router.get("/imports/{import_id}/split")
def split_preview(import_id: UUID, level: int = Query(ge=1, le=2), ctx: Ctx = V):
    return _sections(_get(ctx, import_id).content, level)


def _carry_parts(blocks: list, orig: bytes) -> None:
    """Opaque blocks that point into the import's package (pictures, embedded objects) take those parts along, so a
    snippet made from them exports on its own."""
    with zipfile.ZipFile(io.BytesIO(orig)) as zf:
        rels, ids = rel_map(zf), rel_ids(orig)
        for n in (n for b in blocks for n in D.walk(b)):
            a = n.get("attrs") or {}
            if n.get("type") == "opaque" and isinstance(a.get("xml"), str) and "r:" in a["xml"]:
                try:
                    parts = collect_parts(opaque_element(a["xml"], ids), zf, rels)
                except ValueError:
                    continue
                if parts:
                    a["parts"] = parts


def _prep(blocks: list, orig: bytes | None) -> tuple[dict, list[str]]:
    """Snippet content from import blocks (own package parts carried, `orig` dropped) + warnings."""
    blocks = copy.deepcopy(blocks)
    if orig:
        _carry_parts(blocks, orig)
    blocks = [{**b, "attrs": {k: v for k, v in b["attrs"].items() if k != "orig"}} if "orig" in (b.get("attrs") or {}) else b for b in blocks]
    kinds = sorted({(n.get("attrs") or {}).get("kind", "?") for b in blocks for n in D.walk(b) if n.get("type") == "opaque" and not (n.get("attrs") or {}).get("parts")})
    warn = [f"contains opaque block(s): {', '.join(kinds)} (exported only if self-contained)"] if kinds else []
    return {"type": "doc", "content": blocks}, warn  # `orig` only means something inside the import's own package


def _source(i: Import, blocks: list, title: str) -> dict:
    head = blocks[0] if blocks and blocks[0].get("type") == "heading" else None
    return dict(import_id=str(i.id), heading_text=(D.block_text(head).strip() if head else title)[:200],
                block_range_hash=hashlib.sha1("".join(D.fingerprint(b) for b in blocks).encode()).hexdigest()[:16])


def _variables(content: dict) -> list[str]:
    nodes = [n for b in content["content"] for n in D.walk(b)]
    out = {(n.get("attrs") or {}).get("name") for n in nodes if n.get("type") == "variable"}
    out |= {(n.get("attrs") or {}).get("dynamicList") for n in nodes if n.get("type") == "table"}
    return sorted(v for v in out if v and not re.match(r"(row|doc|org)\.", v) and v not in ("author", "revision_history", "linked_documents"))


def _make(ctx: Ctx, folder, title: str, blocks: list, orig: bytes | None = None, source: dict | None = None) -> ImportSnippetOut:
    content, warn = _prep(blocks, orig)
    s = vcs.new_snippet(ctx, folder, title, content=content, styles=D.style_ids(content), source=source)
    return ImportSnippetOut(id=s.id, folder_id=folder.id, slug=s.slug, title=s.title, head_sha=None, has_draft=True, warnings=warn)


# ---- same title: ask before creating a duplicate; "update existing" writes the user's draft, never a commit ----

def _norm(t: str) -> str:
    return " ".join(t.lower().split())


def _existing(ctx: Ctx, f, title: str):
    """The snippet of the folder with this title (case-insensitive, trimmed) or the slug the title would get."""
    rows = ctx.repo(Snippet).list(Snippet.folder_id == f.id)
    return next((x for x in rows if _norm(x.title) == _norm(title)), None) or next((x for x in rows if x.slug == vcs.slugify(title)), None)


def _my_draft(ctx: Ctx, s: Snippet) -> Draft | None:
    d = vcs.current_draft(ctx, s)
    return d if d and vcs.dirty(ctx, s, d) else None  # a draft that never left HEAD holds nothing to lose


def _info(ctx: Ctx, s: Snippet, f, **extra) -> dict:
    last = (git.log(vcs.repo_of(ctx, s.folder_id), s.id, limit=1) or [{}])[0]
    return dict(**extra, snippet_id=str(s.id), title=s.title, slug=s.slug, head_sha=s.head_sha, folder_id=str(f.id), folder_name=f.name,
                last_commit_at=last.get("date"), last_commit_author=last.get("author_name"), has_draft=_my_draft(ctx, s) is not None)


def _free_title(ctx: Ctx, f, title: str) -> str:
    n = 2
    while _existing(ctx, f, f"{title} ({n})"):
        n += 1
    return f"{title} ({n})"[:200]


def _update(ctx: Ctx, s: Snippet, blocks: list, orig: bytes | None, source: dict) -> ImportSnippetOut:
    """The imported blocks become the user's draft of `s` on top of HEAD (id, slug, title kept; styles and variables
    recomputed from the content). The version bump happens when the user commits."""
    content, warn = _prep(blocks, orig)
    vcs.save_draft(ctx, s, DraftIn(content=content, styles=D.style_ids(content), variables=_variables(content)), rebase=True)
    s.source = source
    return ImportSnippetOut(id=s.id, folder_id=s.folder_id, slug=s.slug, title=s.title, head_sha=s.head_sha, has_draft=True,
                            warnings=warn, action="updated", base_sha=s.head_sha)


def _guard_drafts(ctx: Ctx, snippets: list[Snippet], overwrite: bool) -> None:
    busy = []  # snippets someone else is editing: listed together so the client can offer "request edit rights" per item
    for s in snippets:
        lk = locks.live(ctx.db, ctx.org_id, "snippet", s.id)
        if lk and lk.holder_id != ctx.user.id:
            busy.append(dict(snippet_id=str(s.id), title=s.title, slug=s.slug, holder=locks.holder(ctx.db, lk),
                             message=f"locked by {locks.holder(ctx.db, lk)['name']}"))
    if busy:
        raise HTTPException(409, detail=dict(error="locked", message="locked by " + ", ".join(sorted({b["holder"]["name"] for b in busy})),
                                             snippets=busy))
    held = [s for s in snippets if not overwrite and _my_draft(ctx, s)]
    if held:
        raise HTTPException(409, detail=dict(error="draft_exists", message="you have an unsaved draft on this snippet",
                                             snippets=[dict(snippet_id=str(s.id), title=s.title, slug=s.slug) for s in held]))


@router.post("/imports/{import_id}/conflicts")
def conflicts(import_id: UUID, body: ConflictsIn, ctx: Ctx = V):
    """Per title: the snippet that would clash in the folder (or null)."""
    _get(ctx, import_id)
    f = vcs.get_folder(ctx, body.folder_id)
    return [(lambda x: x and _info(ctx, x, f))(_existing(ctx, f, t)) for t in body.titles]


@router.post("/imports/{import_id}/split", status_code=201, response_model=list[ImportSnippetOut])
def split_apply(import_id: UUID, body: SplitIn, ctx: Ctx = E):
    """`level` (old: all headings up to it) or `headings` (explicit block indexes) + optional `titles` ({index: title},
    -1 = the preface) and `preface`. Sections whose title exists in the folder need an `on_conflict` entry (key = heading
    index, -1 = preface): else 409 name_exists listing them all."""
    i, f = _get(ctx, import_id), vcs.get_folder(ctx, body.folder_id)
    blocks = i.content.get("content") or []
    if (body.level is None) == (body.headings is None):
        raise HTTPException(422, "give either level or headings")
    if body.headings is not None:
        cuts = sorted(set(body.headings))
        bad = [k for k in cuts if not 0 <= k < len(blocks) or blocks[k].get("type") != "heading"]
        if bad:
            raise HTTPException(422, f"not a heading block: {bad[:5]}")
        secs = _sections(i.content, cuts=cuts, preface=body.preface, titles=body.titles)
    else:
        secs = _sections(i.content, body.level)
    if not secs:
        raise HTTPException(422, "nothing to split")
    if len(secs) > MAX_SECTIONS:
        raise HTTPException(422, f"too many sections (max {MAX_SECTIONS})")
    if any(len(str(blocks[s["start"]:s["end"]])) > vcs.MAX_DOC for s in secs):
        raise HTTPException(413, "a section is too large for a snippet")
    ex = {s["key"]: _existing(ctx, f, s["title"]) for s in secs}
    choice = body.on_conflict or {}
    open_ = [s for s in secs if ex[s["key"]] and s["key"] not in choice]
    if open_:
        raise HTTPException(409, detail=dict(error="name_exists", conflicts=[_info(ctx, ex[s["key"]], f, key=s["key"], new_title=s["title"]) for s in open_]))
    _guard_drafts(ctx, [ex[s["key"]] for s in secs if choice.get(s["key"]) == "update_existing"], body.overwrite_draft)
    orig = _orig(ctx, i)
    out = []
    for s in secs:
        part, act, e = blocks[s["start"]:s["end"]], choice.get(s["key"]), ex[s["key"]]
        if act == "skip":
            continue
        src = _source(i, part, s["title"])
        out.append(_update(ctx, e, part, orig, src) if act == "update_existing" else _make(ctx, f, _free_title(ctx, f, s["title"]) if e else s["title"], part, orig, src))
    ctx.db.commit()
    return out


@router.post("/imports/{import_id}/selection-snippet", status_code=201, response_model=ImportSnippetOut)
def selection_snippet(import_id: UUID, body: SelectionIn, ctx: Ctx = E):
    i, f = _get(ctx, import_id), vcs.get_folder(ctx, body.folder_id)
    if body.blocks is not None:  # what the user selected, as the editor holds it (no dependence on the stored copy being current)
        if len(str(body.blocks)) > vcs.MAX_DOC:
            raise HTTPException(413, "selection too large")
        if any(not isinstance(b.get("type"), str) for b in body.blocks):
            raise HTTPException(422, "invalid block")
        try:
            D.check_content({"type": "doc", "content": body.blocks}, rel_ids(_orig(ctx, i)))
        except ValueError as e:
            raise HTTPException(422, f"invalid opaque block: {e}")
        picked = body.blocks
    else:
        blocks = i.content.get("content") or []
        if body.end > len(blocks) or body.start >= body.end:
            raise HTTPException(422, f"block range out of bounds (0..{len(blocks)})")
        picked = blocks[body.start:body.end]
    e = _existing(ctx, f, body.title)
    if e and not body.on_conflict:
        raise HTTPException(409, detail=dict(error="name_exists", conflicts=[_info(ctx, e, f, new_title=body.title)]))
    src = _source(i, picked, body.title)
    if e and body.on_conflict == "skip":
        return ImportSnippetOut(id=e.id, folder_id=f.id, slug=e.slug, title=e.title, head_sha=e.head_sha, action="skipped")
    if e and body.on_conflict == "update_existing":
        _guard_drafts(ctx, [e], body.overwrite_draft)
        out = _update(ctx, e, picked, _orig(ctx, i), src)
    else:
        out = _make(ctx, f, _free_title(ctx, f, body.title) if e else body.title, picked, _orig(ctx, i), src)
    ctx.db.commit()
    return out


# ---- update snippets from (another) import: match headings to the folder's snippets, show what changed, write drafts ----

def _match(rows: list[dict], snippets: list[Snippet]) -> None:
    """Fill row['match'] = (snippet, how, score): same title/slug, remembered source heading (a renamed snippet), then fuzzy title (>= 0.7);
    a snippet is matched to one heading only."""
    used: set = set()
    for how, test in (("title", lambda r, x: _norm(x.title) == _norm(r["title"]) or x.slug == vcs.slugify(r["title"])),
                      ("source", lambda r, x: _norm((x.source or {}).get("heading_text") or "\0") == _norm(r["title"]))):
        for r in rows:
            x = None if r.get("match") else next((x for x in snippets if x.id not in used and test(r, x)), None)
            if x:
                r["match"] = (x, how, 1.0)
                used.add(x.id)
    for r in rows:
        if not r.get("match"):
            sc = [(difflib.SequenceMatcher(None, _norm(r["title"]), _norm(x.title)).ratio(), x) for x in snippets if x.id not in used]
            best = max(sc, key=lambda t: t[0], default=None)
            if best and best[0] >= 0.7:
                r["match"] = (best[1], "fuzzy", round(best[0], 2))
                used.add(best[1].id)


def _changes(ctx: Ctx, s: Snippet, content: dict) -> dict:
    """Blocks of the import that differ from the snippet's HEAD (no HEAD yet: everything is new)."""
    h = vcs.head_version(ctx, s)
    asset, cache = _asset(ctx), {}

    def fp(blk: dict) -> str:  # modulo what the editor adds when it re-saves a block, and with images by their bytes (a re-import stores new ids)
        blk = copy.deepcopy(blk)
        for n in D.walk(blk):
            a = n.get("attrs") or {}
            if n.get("type") == "image" and a.get("imageId"):
                if a["imageId"] not in cache:
                    data = asset(None, a["imageId"])
                    cache[a["imageId"]] = hashlib.sha1(data).hexdigest() if data else a["imageId"]
                a["imageId"] = cache[a["imageId"]]
        return D.fingerprint(blk)
    a, b = [fp(x) for x in (h["content"]["content"] if h else [])], [fp(x) for x in content["content"]]
    n = dict(added=0, removed=0, changed=0)
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        k = min(i2 - i1, j2 - j1) if tag == "replace" else 0
        n["changed"] += k
        n["removed"] += (i2 - i1) - k
        n["added"] += (j2 - j1) - k
    return dict(**n, same=not any(n.values()))


def _plan_sections(i: Import, headings: list[int]) -> list[dict]:
    blocks = i.content.get("content") or []
    cuts = sorted(set(headings))
    if not cuts or any(not 0 <= k < len(blocks) or blocks[k].get("type") != "heading" for k in cuts):
        raise HTTPException(422, "give heading block indexes")
    return _sections(i.content, cuts=cuts, preface=False)


@router.post("/imports/{import_id}/update-plan")
def update_plan(import_id: UUID, body: UpdatePlanIn, ctx: Ctx = V):
    """One row per selected heading: the auto-matched snippet of the folder (`how`: source | title | fuzzy), what would change
    against its HEAD, and the effective `target` (the user's `targets` choice wins over the match)."""
    i, f = _get(ctx, import_id), vcs.get_folder(ctx, body.folder_id)
    secs, blocks, orig = _plan_sections(i, body.headings), i.content.get("content") or [], _orig(ctx, i)
    snippets = ctx.repo(Snippet).list(Snippet.folder_id == f.id)
    by_id = {str(x.id): x for x in snippets}
    rows = [dict(key=s["key"], title=s["title"], start=s["start"], end=s["end"], blocks=s["blocks"], level=s["level"]) for s in secs]
    _match(rows, snippets)
    out = []
    for r in rows:
        t = (body.targets or {}).get(r["key"])
        if t is None:
            t = str(r["match"][0].id) if r.get("match") else "new"
        if t not in ("new", "skip") and t not in by_id:
            raise HTTPException(404, "snippet not found")
        m = r.pop("match", None)
        x = by_id.get(t)
        out.append(dict(**r, match=m and dict(snippet_id=str(m[0].id), how=m[1], score=m[2]), target=t,
                        changes=x and _changes(ctx, x, _prep(blocks[r["start"]:r["end"]], orig)[0]), has_draft=bool(x and _my_draft(ctx, x))))
    return out


@router.post("/imports/{import_id}/update-apply", response_model=list[ImportSnippetOut])
def update_apply(import_id: UUID, body: UpdateApplyIn, ctx: Ctx = E):
    """Write the chosen rows: `update` = the user's draft of that snippet (409 draft_exists unless overwrite_draft), `create` =
    a new snippet draft (title suffixed if taken), `skip`. The sections are cut at the non-skipped headings only."""
    i, f = _get(ctx, import_id), vcs.get_folder(ctx, body.folder_id)
    items = {it.key: it for it in body.items if it.action != "skip"}
    if not items:
        raise HTTPException(422, "nothing selected")
    secs, blocks, orig = _plan_sections(i, list(items)), i.content.get("content") or [], _orig(ctx, i)
    targets = {}
    for k, it in items.items():
        if it.action == "update":
            targets[k] = vcs.get_snippet(ctx, it.snippet_id) if it.snippet_id else None
            if not targets[k] or targets[k].folder_id != f.id:
                raise HTTPException(404, "snippet not found")
    if len({x.id for x in targets.values()}) != len(targets):
        raise HTTPException(422, "a snippet can only be updated from one heading")
    _guard_drafts(ctx, list(targets.values()), body.overwrite_draft)
    out = []
    for s in secs:
        end = items[s["key"]].end or s["end"]
        if not s["start"] < end <= len(blocks):
            raise HTTPException(422, "block range out of bounds")
        part = blocks[s["start"]:end]
        src = _source(i, part, s["title"])
        if s["key"] in targets:
            out.append(_update(ctx, targets[s["key"]], part, orig, src))
        else:
            out.append(_make(ctx, f, _free_title(ctx, f, s["title"]) if _existing(ctx, f, s["title"]) else s["title"], part, orig, src))
    ctx.db.commit()
    return out


@router.post("/imports/{import_id}/base-document", status_code=201)
def use_as_base(import_id: UUID, body: ImportBaseIn, ctx: Ctx = A):
    i = _get(ctx, import_id)
    if bool(body.name) == bool(body.base_document_id):
        raise HTTPException(422, "give either name (new base document) or base_document_id (new version)")
    bd = get_base(ctx, body.base_document_id) if body.base_document_id else add_base(ctx, body.name)
    try:
        data = D.as_base_doc(_orig(ctx, i), i.content, _asset(ctx))
    except Exception as e:
        raise HTTPException(422, f"export failed: {type(e).__name__}: {e}")
    v = _store(ctx, bd, data, i.filename, f"From import '{i.title}'", [*docx_vars.table_decls(i.content), *(i.variables or [])])
    ctx.db.commit()
    return dict(base_document_id=bd.id, name=bd.name, version=v.number, sha=v.sha, placeholders=v.placeholders)


# ---- variables: per-document values (cover, header/footer, fields) -> {{ name }} ----

def _forms(n: dict) -> list:
    """FORMTEXT field nodes of a paragraph/heading, in order (the cover's form fields: variable candidates like their OOXML twins)."""
    return [x for x in (n.get("content") or []) if x.get("type") == "field" and (x.get("attrs") or {}).get("code") == "FORMTEXT"] \
        if n.get("type") in ("paragraph", "heading") else []


def _form_xml(n: dict):
    """The paragraph as the finder sees it: text runs and FORMTEXT complex fields (result run = the cached text, the bookmark around it)."""
    esc = lambda t: (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    r = lambda inner: f"<w:r>{inner}</w:r>"
    out = []
    for c in n.get("content") or []:
        a = c.get("attrs") or {}
        if c.get("type") != "field" or a.get("code") != "FORMTEXT":
            out.append(r(f'<w:t>{esc(c.get("text"))}</w:t>'))
            continue
        out.append(f'<w:bookmarkStart w:id="1" w:name="{esc(a["bm"])}"/>' if a.get("bm") else "")
        out += [r('<w:fldChar w:fldCharType="begin"/>'), r("<w:instrText> FORMTEXT </w:instrText>"), r('<w:fldChar w:fldCharType="separate"/>'),
                r(f'<w:t>{esc(a.get("text"))}</w:t>'), r('<w:fldChar w:fldCharType="end"/>'), '<w:bookmarkEnd w:id="1"/>' if a.get("bm") else ""]
    sid = re.sub(r"[^A-Za-z0-9_-]", "", (n.get("attrs") or {}).get("styleId") or "Normal")
    return docx_vars.parse(f'<w:p xmlns:w="{docx_vars.W_NS}"><w:pPr><w:pStyle w:val="{sid}"/></w:pPr>{"".join(out)}</w:p>'.encode())


def _units(ctx: Ctx, i: Import):
    """(units for docx_vars, write-back list) of the import: header/footer parts of the package and the front matter
    blocks (everything before the first Heading 1). Opaque XML and plain paragraphs are edited in place."""
    hf = docx_vars.hf_roots(_orig(ctx, i))
    units = [(("header" if "header" in n else "footer"), r, True) for n, r in hf.items()]
    blocks = i.content.get("content") or []
    back = []
    for b in blocks[:D.front_end(blocks)]:
        if (b.get("attrs") or {}).get("sec") or b.get("type") == "toc":
            continue  # marked sections and generated lists are structure, never per-document values
        if b.get("type") in ("paragraph", "heading") and D.block_text(b).strip() and not _forms(b):
            p = docx_vars.parse(('<w:p xmlns:w="%s"><w:pPr><w:pStyle w:val="%s"/></w:pPr><w:r><w:t/></w:r></w:p>'
                                 % (docx_vars.W_NS, re.sub(r"[^A-Za-z0-9_-]", "", (b.get("attrs") or {}).get("styleId") or "Normal"))).encode())
            p.find(".//" + docx_vars.W + "t").text = D.block_text(b)
            units.append(("cover", p, True))
            back.append((b, p, D.block_text(b)))
        labels = {}  # opaque paragraphs in table cells: the label is the cell before (a form row: "Revision:" | field)
        for t in (n for n in D.walk(b) if n.get("type") == "table"):
            for row in t.get("content") or []:
                prev = ""
                for cell in row.get("content") or []:
                    labels.update({id(n): prev for n in D.walk(cell)})
                    prev = D.block_text(cell).strip()
        for n in D.walk(b):
            if _forms(n) or n.get("type") == "opaque" and isinstance((n.get("attrs") or {}).get("xml"), str):
                r = _form_xml(n) if _forms(n) else docx_vars.parse(n["attrs"]["xml"].encode())
                if id(n) in labels:  # wrap like the table it came from, so label/`in a table` checks see it
                    row = docx_vars.parse(('<w:tr xmlns:w="%s"><w:tc><w:p/></w:tc><w:tc/></w:tr>' % docx_vars.W_NS).encode())
                    row[0][0].append(docx_vars.parse(('<w:r xmlns:w="%s"><w:t/></w:r>' % docx_vars.W_NS).encode()))
                    row[0][0][0][0].text = labels[id(n)]
                    row[1].append(r)
                    units.append(("cover", row, True))
                else:
                    units.append(("cover", r, True))
                back.append((n, r, n["attrs"]["xml"] if n.get("type") == "opaque" else ""))
    return units, hf, back


@router.get("/imports/{import_id}/variables")
def import_variables(import_id: UUID, ctx: Ctx = V):
    i = _get(ctx, import_id)
    return dict(candidates=docx_vars.scan(_units(ctx, i)[0]), declared=i.variables or [],
                has_slot=any(D.is_slot(b) for b in i.content.get("content") or []))


@router.post("/imports/{import_id}/variables")
def apply_import_variables(import_id: UUID, body: VarPicks, ctx: Ctx = E):
    """Replace the chosen values by `{{ name }}` (runs keep their formatting) and declare them with the value as default."""
    i = _get(ctx, import_id)
    units, hf, back = _units(ctx, i)
    n = docx_vars.apply(units, {p.value: p.name for p in body.items})
    if not n:
        raise HTTPException(422, "none of the values were found")
    for node, root, old in back:
        if _forms(node):  # the new result texts go back into the field nodes, in order
            for fld, f in zip(_forms(node), [f for f in docx_vars.fields(root) if f["kw"] == "FORMTEXT"]):
                if fld["attrs"].get("text") != f["text"]:
                    fld["attrs"]["text"] = f["text"]
                    (node.get("attrs") or {}).pop("orig", None)
        elif node.get("type") == "opaque":
            new = etree.tostring(root, encoding="unicode")
            if new != old:
                node["attrs"]["xml"] = new
        elif (t := docx_vars.text(root)) != old:  # plain paragraph: one text run carrying the first run's marks
            marks = next((c.get("marks") for c in node.get("content") or [] if c.get("type") == "text"), None)
            node["content"] = [{"type": "text", "text": t, **({"marks": marks} if marks else {})}]
            (node.get("attrs") or {}).pop("orig", None)
    for doc in ((i.content or {}).get("hf") or {}).values():  # edited header/footer parts are written over the package parts: replace there too
        for n in D.walk(doc):
            if n.get("type") == "text":
                for p in body.items:
                    n["text"] = n["text"].replace(p.value, "{{ %s }}" % p.name)
    D.check_content(i.content, rel_ids(_orig(ctx, i)))
    flag_modified(i, "content")
    import_path(ctx.org_id, i.id).write_bytes(docx_vars.write_parts(_orig(ctx, i), hf))
    new = {d["name"]: d for d in [*(i.variables or []), *docx_vars.picks_decls(body.items)]}
    i.variables = list(new.values())
    ctx.db.commit()
    return dict(replaced=n, declared=i.variables)


# ---- editing a base document in the import editor (round 9) ----
# A base-edit session is an Import row linked to the base document (per user): the head version's DOCX is parsed like an
# upload (unedited blocks keep their original XML, every package part stays), autosave = PUT /imports/{id}, commit = a new
# version through the same `_store` as uploads (author = the user, one git commit in _base.git).

def _load_base(ctx: Ctx, data: bytes) -> tuple[dict, list]:
    def store(b: bytes):
        try:
            return str(images.save(ctx, b).id)
        except HTTPException:
            return None
    try:
        content, warnings = D.parse(data, store)
    except base_docs.BadDocx as e:
        raise HTTPException(422, str(e))
    if len(str(content)) > vcs.MAX_DOC:
        raise HTTPException(413, "document too large to edit")
    return content, warnings


def _fps(content: dict) -> list[str]:
    return [D.fingerprint(b) for b in content.get("content") or []]


def _session(ctx: Ctx, i: Import):
    """(base document, head version) of a base-edit session; 404 for a plain import."""
    if not i.base_document_id:
        raise HTTPException(404, "not a base document editing session")
    bd = get_base(ctx, i.base_document_id)
    return bd, _versions(ctx, bd)[0]


@router.post("/base-documents/{id}/edit")
def base_edit(id: UUID, ctx: Ctx = A):
    """Open (or resume) the caller's editing session on the current version. `stale`: a newer version appeared since it started."""
    bd = get_base(ctx, id)
    head, data = _head_docx(ctx, bd)
    i = ctx.repo(Import).first(Import.base_document_id == bd.id, Import.created_by == ctx.user.id)
    if not i:
        content, warnings = _load_base(ctx, data)
        i = ctx.repo(Import).add(title=f"Edit: {bd.name}"[:200], filename=head.filename, content=content, warnings=warnings, created_by=ctx.user.id,
                                 base_document_id=bd.id, base_sha=head.sha, base_fp=_fps(content))
        p = import_path(ctx.org_id, i.id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        ctx.db.commit()
        return dict(import_id=i.id, created=True, stale=False)
    return dict(import_id=i.id, created=False, stale=i.base_sha != head.sha)


def _diff(i: Import) -> dict:
    """Changed blocks against the version the session started from: by fingerprint (`orig.h` semantics), plus edited header/footer parts."""
    a, b = i.base_fp or [], _fps(i.content)
    n = dict(added=0, removed=0, changed=0)
    items = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        k = min(i2 - i1, j2 - j1) if tag == "replace" else 0
        n["changed"] += k
        n["removed"] += (i2 - i1) - k
        n["added"] += (j2 - j1) - k
        for x, op in [*((j1 + y, "changed") for y in range(k)), *((j1 + k + y, "added") for y in range(j2 - j1 - k))]:
            blk = i.content["content"][x]
            items.append(dict(op=op, index=x, type=blk.get("type"), text=D.block_text(blk)[:80]))
    return dict(**n, headers_footers=len((i.content or {}).get("hf") or {}), blocks=items[:50])


@router.get("/imports/{import_id}/base-edit")
def base_edit_info(import_id: UUID, ctx: Ctx = A):
    i = _get(ctx, import_id)
    bd, head = _session(ctx, i)
    vs = _versions(ctx, bd)
    started = next((v for v in vs if v.sha == i.base_sha), None)
    return dict(base_document=dict(id=bd.id, name=bd.name), started_number=started and started.number, head_number=head.number,
                stale=i.base_sha != head.sha, diff=_diff(i),
                versions=[dict(number=v.number, sha=v.sha, message=n.message, created_at=v.created_at, filename=v.filename)
                          for v, n in zip(vs, named(ctx, vs))])


@router.post("/imports/{import_id}/base-commit", status_code=201)
def base_commit(import_id: UUID, body: BaseCommitIn, ctx: Ctx = A):
    """A new version of the base document from the session; the draft is consumed. 409 `base_changed` when another version was
    committed meanwhile (resend with override=true to commit on top of it anyway)."""
    i = _get(ctx, import_id)
    bd, _ = _session(ctx, i)
    ctx.db.execute(text("select pg_advisory_xact_lock(hashtext(:k))"), {"k": f"base:{ctx.org_id}"})  # check + commit under one lock
    head = _versions(ctx, bd)[0]
    if head.sha != i.base_sha and not body.override:
        raise HTTPException(409, detail=dict(error="base_changed", message=f"version {head.number} was committed since this draft started",
                                             head_number=head.number, head_sha=head.sha))
    try:
        D.check_content(i.content, rel_ids(_orig(ctx, i)))
        data, warnings = D.export(_orig(ctx, i), i.content, _asset(ctx))  # no render: {{ }} and {%tr %} stay in the file
    except ValueError as e:
        raise HTTPException(422, f"invalid block: {e}")
    except Exception as e:
        raise HTTPException(422, f"export failed: {type(e).__name__}: {e}")
    v = _store(ctx, bd, data, head.filename, body.message, docx_vars.table_decls(i.content))
    notes = [*warnings, *([] if v.placeholders.get("content") else ["no {{ CONTENT }} placeholder: documents cannot place their blocks in this base document"])]
    ctx.repo(Import).delete(i)
    ctx.db.commit()
    import_path(ctx.org_id, i.id).unlink(missing_ok=True)
    return dict(version=v.number, sha=v.sha, placeholders=v.placeholders, variables=[dict(name=d["name"], type=d["type"]) for d in v.variables],
                warnings=notes)


@router.post("/imports/{import_id}/base-restore")
def base_restore(import_id: UUID, body: BaseRestoreIn, ctx: Ctx = A):
    """Replace the draft by an older version of the base document (committing it makes that content the new version)."""
    i = _get(ctx, import_id)
    bd, head = _session(ctx, i)
    v = ctx.repo(BaseDocVersion).first(BaseDocVersion.base_document_id == bd.id, BaseDocVersion.sha == body.sha)
    data = v and git.show(git.base_repo_path(ctx.org_id), v.sha, f"base/{bd.id}.docx")
    if not data:
        raise HTTPException(404, "version not found")
    i.content, i.warnings = _load_base(ctx, data)
    i.base_fp, i.base_sha = _fps(i.content), head.sha  # the diff then shows what differs from the version the draft started from
    flag_modified(i, "content")
    import_path(ctx.org_id, i.id).write_bytes(data)
    ctx.db.commit()
    return dict(**_meta(i), content=i.content, warnings=i.warnings)
