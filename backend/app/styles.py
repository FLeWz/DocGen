"""Org style set: built-in defaults, seeding and the resolver.

Content nodes reference styles by `style_id` (Word-style ids: "Normal", "Heading1"), never by uuid,
so a base document's styles.xml (milestone 4) can supply the same ids."""
from .models import Style
from .scoping import OrgRepo

_P = dict(kind="paragraph")
_H = lambda n, size, before: dict(kind="heading", level=n, font_size=size, bold=True, color="#1f3864",
                                  numbering="decimal", space_before=before, space_after=6)
DEFAULTS: list[dict] = [
    dict(style_id="Normal", name="Normal", font_family="Calibri", font_size=11, space_after=8, **_P),
    dict(style_id="Title", name="Title", font_family="Calibri", font_size=26, bold=True, color="#1f3864",
         space_after=12, **_P),
    dict(style_id="Heading1", name="Heading 1", **_H(1, 18, 18)),
    dict(style_id="Heading2", name="Heading 2", **_H(2, 14, 14)),
    dict(style_id="Heading3", name="Heading 3", **_H(3, 12, 12)),
    dict(style_id="Heading4", name="Heading 4", **_H(4, 11, 10)),
    dict(style_id="Quote", name="Quote", italic=True, color="#595959", font_size=11, space_before=6, space_after=6, **_P),
    dict(style_id="Code", name="Code", font_family="Courier New", font_size=10, space_after=6, **_P),
    dict(style_id="ListParagraph", name="List Paragraph", space_after=2, **_P),
    dict(style_id="TableGrid", name="Table Grid", kind="table"),
]


def seed(repo: OrgRepo) -> None:
    for d in DEFAULTS:
        repo.add(builtin=True, **d)


def resolve(repo: OrgRepo, base_doc=None) -> list[Style]:
    """Styles for a document. Milestone 4: when `base_doc` has its own style list, its styles override
    org styles with the same style_id and org styles fill the gaps (or replace the set entirely --
    see DECISIONS.md). Until then there is only the org set."""
    return repo.list()
