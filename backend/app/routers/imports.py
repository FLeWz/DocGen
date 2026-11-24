import re
from uuid import UUID

from lxml import etree
from sqlalchemy.orm.attributes import flag_modified
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile

from datetime import datetime, timezone

from .. import base_docs, docx_export, docx_hf, docx_import as D, docx_vars, vcs
from ..deps import Ctx, require
from ..files import image_path, import_path
from ..models import BaseDocument, Image, Import, Organization, Role
from ..ooxml import rel_ids
from ..schemas import VarPicks, ImportBaseIn, ImportPut, ImportSnippetOut, SelectionIn, SplitIn
from . import images
from .base_documents import _get as get_base, _read, _store, add_base

router = APIRouter(tags=["imports"])
V = Depends(require(Role.viewer))
E = Depends(require(Role.editor))
A = Depends(require(Role.admin))
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MAX_SECTIONS = 1000


def _get(ctx: Ctx, id: UUID) -> Import:
    imp = ctx.repo(Import).get(id)
    if not imp:
        raise HTTPException(404, "import not found")
    return imp


def _orig(ctx: Ctx, imp: Import) -> bytes:
    return import_path(ctx.org_id, imp.id).read_bytes()


def _meta(i: Import) -> dict:
    return dict(id=i.id, title=i.title, filename=i.filename, created_at=i.created_at, updated_at=i.updated_at)


@router.post("/import/docx", status_code=201)
def import_docx(file: UploadFile = File(), title: str | None = Form(default=None, max_length=200), ctx: Ctx = E):
    data = _read(file)
    try:  # same gate as base documents: zip structure, bombs, XXE, macros
        base_docs.validate(data)

        def store(b: bytes):
            try:
                return str(images.save(ctx, b).id)
            except HTTPException:
                return None
        content, warnings = D.parse(data, store)
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
    return [_meta(i) for i in sorted(ctx.repo(Import).list(), key=lambda i: i.created_at, reverse=True)]


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
        out.append(dict(title=title[:200], start=s, end=e, blocks=e - s, level=(head or {}).get("attrs", {}).get("level")))
    return out


@router.get("/imports/{import_id}/headings")
def headings(import_id: UUID, ctx: Ctx = V):
    return _headings(_get(ctx, import_id).content)


@router.get("/imports/{import_id}/split")
def split_preview(import_id: UUID, level: int = Query(ge=1, le=2), ctx: Ctx = V):
    return _sections(_get(ctx, import_id).content, level)


def _make(ctx: Ctx, folder, title: str, blocks: list) -> ImportSnippetOut:
    blocks = [{**b, "attrs": {k: v for k, v in b["attrs"].items() if k != "orig"}} if "orig" in (b.get("attrs") or {}) else b for b in blocks]
    content = {"type": "doc", "content": blocks}  # `orig` only means something inside the import's own package
    s = vcs.new_snippet(ctx, folder, title, content=content, styles=D.style_ids(content))
    kinds = sorted({(n.get("attrs") or {}).get("kind", "?") for b in blocks for n in D.walk(b) if n.get("type") == "opaque"})
    warn = [f"contains opaque block(s): {', '.join(kinds)} (exported only if self-contained)"] if kinds else []
    return ImportSnippetOut(id=s.id, folder_id=folder.id, slug=s.slug, title=s.title, head_sha=None, has_draft=True, warnings=warn)


@router.post("/imports/{import_id}/split", status_code=201, response_model=list[ImportSnippetOut])
def split_apply(import_id: UUID, body: SplitIn, ctx: Ctx = E):
    """`level` (old: all headings up to it) or `headings` (explicit block indexes) + optional `titles` ({index: title},
    -1 = the preface) and `preface`."""
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
    out = [_make(ctx, f, s["title"], blocks[s["start"]:s["end"]]) for s in secs]
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
    out = _make(ctx, f, body.title, picked)
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
        if b.get("type") in ("paragraph", "heading") and D.block_text(b).strip():
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
            if n.get("type") == "opaque" and isinstance((n.get("attrs") or {}).get("xml"), str):
                r = docx_vars.parse(n["attrs"]["xml"].encode())
                if id(n) in labels:  # wrap like the table it came from, so label/`in a table` checks see it
                    row = docx_vars.parse(('<w:tr xmlns:w="%s"><w:tc><w:p/></w:tc><w:tc/></w:tr>' % docx_vars.W_NS).encode())
                    row[0][0].append(docx_vars.parse(('<w:r xmlns:w="%s"><w:t/></w:r>' % docx_vars.W_NS).encode()))
                    row[0][0][0][0].text = labels[id(n)]
                    row[1].append(r)
                    units.append(("cover", row, True))
                else:
                    units.append(("cover", r, True))
                back.append((n, r, n["attrs"]["xml"]))
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
        if node.get("type") == "opaque":
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
