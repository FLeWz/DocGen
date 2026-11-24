"""'Render as Word does': the editor content through the real DOCX exporter, then Gotenberg/LibreOffice -> PDF.
Nothing is stored; the PDF is the fidelity ground truth next to the fast HTML preview."""
import re
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from .. import base_docs, docs, docx_export, docx_import as D, export, vcs
from ..deps import Ctx, require
from ..files import image_path, import_path
from ..git import repo as git
from ..models import BaseDocument, Image, Import, Organization, Role
from ..ooxml import rel_ids

router = APIRouter(tags=["render"])


class RenderIn(BaseModel):
    content: dict
    import_id: UUID | None = None   # export into this import's package (its styles, page setup, header/footer)
    base_doc: UUID | None = None    # else into this base document's head version; neither = the bundled default


@router.post("/render/pdf")
def render_pdf(body: RenderIn, ctx: Ctx = Depends(require(Role.viewer))):
    if len(str(body.content)) > vcs.MAX_DOC:
        raise HTTPException(413, "content too large")

    def asset(_c, iid):
        ok = re.fullmatch(r"[0-9a-f-]{36}", str(iid))
        img = ctx.repo(Image).get(UUID(str(iid))) if ok else None
        return img and image_path(ctx.org_id, img.id, img.ext).read_bytes()
    try:
        if body.import_id:
            imp = ctx.repo(Import).get(body.import_id)
            if not imp:
                raise HTTPException(404, "import not found")
            orig = import_path(ctx.org_id, imp.id).read_bytes()
            D.check_content(body.content, rel_ids(orig))
            docx, warnings = D.export(orig, body.content, asset)
        else:
            if body.base_doc:
                bd = ctx.repo(BaseDocument).get(body.base_doc)
                if not bd:
                    raise HTTPException(404, "base document not found")
                base = git.show(git.base_repo_path(ctx.org_id), docs.base_head(ctx, bd).sha, f"base/{bd.id}.docx")
            else:
                base = base_docs.default_docx()
            D.check_content(body.content, None)
            org = ctx.db.get(Organization, ctx.org_id)
            spec = dict(title="Preview", blocks=[dict(type="snippet_ref", slot="CONTENT", pin=dict(snippet_id="-"))],
                        variables=dict(declarations=[], values={}), links=[], revision_history=[],
                        built_ins={"doc.title": "Preview", "doc.version": "draft", "org.name": org.name, "author": ctx.user.name,
                                   "doc.date": datetime.now(timezone.utc).date().isoformat()})
            docx, warnings = docx_export.build(spec, base, lambda pin: (body.content, "preview"), lambda pin, slug, iid: asset(None, iid))
        pdf = export.docx_to_pdf(docx)
    except ValueError as e:
        raise HTTPException(422, f"render failed: {e}")
    except HTTPException:
        raise
    except Exception as e:  # python-docx on content the editor produced but Word structure rejects
        raise HTTPException(422, f"render failed: {type(e).__name__}: {e}")
    return Response(pdf, media_type="application/pdf", headers={"X-Content-Type-Options": "nosniff",
                                                                "X-Export-Warnings": str(len(warnings))})
