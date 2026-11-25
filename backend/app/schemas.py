import json
import re
from datetime import datetime
from uuid import UUID

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel as _B, ConfigDict, EmailStr, Field, field_validator, model_validator

from .dates import parse_date


class BaseModel(_B):
    model_config = ConfigDict(from_attributes=True)


from .models import Role

Password = Field(min_length=10, max_length=128)
Name = Field(min_length=1, max_length=100)


class SignupIn(BaseModel):
    email: EmailStr
    password: str = Password
    name: str = Name
    org_name: str = Name


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)


class UserOut(BaseModel):
    id: UUID
    email: str
    name: str


class OrgOut(BaseModel):
    id: UUID
    name: str
    role: Role


class MeOut(BaseModel):
    user: UserOut
    org: OrgOut | None


class OrgCreateIn(BaseModel):
    name: str = Name


class SwitchIn(BaseModel):
    org_id: UUID  # the one place a client names an org; checked against own memberships


class MemberOut(BaseModel):
    user_id: UUID
    email: str
    name: str
    role: Role
    joined_at: datetime


class RoleIn(BaseModel):
    role: Role


class InviteCreateIn(BaseModel):
    email: EmailStr
    role: Role


class InviteOut(BaseModel):
    id: UUID
    email: str
    role: Role
    status: str  # pending | accepted | revoked | expired
    expires_at: datetime
    created_at: datetime
    email_sent: bool | None = None


class TokenIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)


class InviteLookupOut(BaseModel):
    org_name: str
    email: str
    role: Role
    status: str
    user_exists: bool


class AcceptIn(TokenIn):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    password: str | None = Field(default=None, min_length=10, max_length=128)


SLUG = r"^[a-z0-9][a-z0-9-]{0,63}$"


class FolderCreateIn(BaseModel):
    name: str = Name
    parent_id: UUID | None = None


class FolderPatchIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    parent_id: UUID | None = None  # explicit null = move to root (see model_fields_set)


class FolderOut(BaseModel):
    id: UUID
    parent_id: UUID | None
    name: str


class SnippetCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    slug: str | None = Field(default=None, pattern=SLUG)


class SnippetOut(BaseModel):
    id: UUID
    folder_id: UUID
    slug: str
    title: str
    head_sha: str | None
    has_draft: bool = False


class DraftIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    slug: str | None = Field(default=None, pattern=SLUG)
    content: dict  # ProseMirror/TipTap doc: {"type": "doc", "content": [...]}
    styles: list[str] | None = Field(default=None, max_length=200)
    variables: list[str] | None = Field(default=None, max_length=200)


class RestoreIn(BaseModel):
    version: str = Field(max_length=64)  # sha or tag


class CommitIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    snippet_ids: list[UUID] = Field(default=[], max_length=500)
    document_ids: list[UUID] = Field(default=[], max_length=500)

    @model_validator(mode="after")
    def _something(self):
        if not (self.snippet_ids or self.document_ids):
            raise ValueError("nothing to commit")
        return self


class TagIn(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    sha: str | None = Field(default=None, max_length=64)  # default HEAD


Hex = r"^#[0-9a-fA-F]{6}$"


class StyleIn(BaseModel):
    style_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9]{0,39}$")
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["paragraph", "heading", "character", "table"]
    level: int | None = Field(default=None, ge=1, le=9)
    color: str | None = Field(default=None, pattern=Hex)
    font_size: float | None = Field(default=None, gt=0, le=200)
    font_family: str | None = Field(default=None, pattern=r"^[A-Za-z0-9 _-]{1,60}$")
    numbering: Literal["none", "decimal"] = "none"
    space_before: float = Field(default=0, ge=0, le=200)
    space_after: float = Field(default=0, ge=0, le=200)
    bold: bool = False
    italic: bool = False

    @model_validator(mode="after")
    def _heading_level(self):
        if (self.kind == "heading") != (self.level is not None):
            raise ValueError("level is required for (and only for) heading styles")
        return self


class StylePatchIn(BaseModel):  # style_id/kind are fixed after creation: content references them
    name: str | None = Field(default=None, min_length=1, max_length=80)
    level: int | None = Field(default=None, ge=1, le=9)
    color: str | None = Field(default=None, pattern=Hex)
    font_size: float | None = Field(default=None, gt=0, le=200)
    font_family: str | None = Field(default=None, pattern=r"^[A-Za-z0-9 _-]{1,60}$")
    numbering: Literal["none", "decimal"] | None = None
    space_before: float | None = Field(default=None, ge=0, le=200)
    space_after: float | None = Field(default=None, ge=0, le=200)
    bold: bool | None = None
    italic: bool | None = None


class StyleOut(StyleIn):
    id: UUID
    builtin: bool


class ImageOut(BaseModel):
    id: UUID
    mime: str
    size: int
    width: int
    height: int


# ---- base documents / documents (milestone 4) ----
class BaseDocVersionOut(BaseModel):
    id: UUID
    sha: str
    number: int
    filename: str
    size: int
    message: str
    placeholders: dict
    variables: list = []
    created_at: datetime


class BaseDocOut(BaseModel):
    id: UUID
    name: str
    head: BaseDocVersionOut | None = None
    version_count: int = 0
    used_by: int = 0  # documents (committed or draft) pinning it
    created_at: datetime | None = None
    updated_at: datetime | None = None


class BaseDocRename(BaseModel):
    name: str = Field(min_length=1, max_length=100)


SlotName = Field(default="CONTENT", pattern=r"^[A-Za-z_]\w{0,39}$")
_Bid = Field(default=None, max_length=36)


class Pin(BaseModel):
    type: Literal["commit", "tag", "latest"]
    value: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _value(self):
        if (self.type == "latest") == bool(self.value):
            raise ValueError("value is required for commit/tag pins and not allowed for latest")
        return self


class SnippetRefBlock(BaseModel):
    type: Literal["snippet_ref"]
    id: str | None = _Bid
    slot: str = SlotName
    folder_id: UUID
    snippet_id: UUID
    version: Pin


class HeadingBlock(BaseModel):
    type: Literal["heading"]
    id: str | None = _Bid
    slot: str = SlotName
    text: str = Field(min_length=1, max_length=200)
    level: int = Field(ge=1, le=4)
    styleId: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9]{0,39}$")
    numbered: bool = False


class PageBreakBlock(BaseModel):
    type: Literal["page_break"]
    id: str | None = _Bid
    slot: str = SlotName


class TocBlock(BaseModel):
    type: Literal["toc"]
    id: str | None = _Bid
    slot: str = SlotName
    kind: Literal["toc", "tof", "tot"] = "toc"  # contents / list of figures / list of tables
    title: str = Field(default="", max_length=200)
    levels: str = Field(default="1-4", pattern=r"^[1-4]-[1-4]$")
    label: str = Field(default="", pattern=r"^[A-Za-z0-9 ._-]{0,60}$")  # SEQ identifier of tof/tot ("" = Figure/Table)


PARA_NODES = {"doc", "paragraph", "heading", "bulletList", "orderedList", "listItem", "text", "variable", "hardBreak", "field", "footnote"}
PARA_MAX_CHARS, PARA_MAX_NODES, PARA_MAX_DEPTH = 100_000, 3000, 12


def check_para(doc: dict) -> dict:
    """A paragraph block's TipTap doc: known node types only (no tables/images/opaque XML), bounded size and depth."""
    if len(json.dumps(doc)) > PARA_MAX_CHARS:
        raise ValueError(f"paragraph block larger than {PARA_MAX_CHARS} characters")
    n = [0]

    def walk(x, depth):
        if not isinstance(x, dict) or x.get("type") not in PARA_NODES or depth > PARA_MAX_DEPTH:
            raise ValueError(f"paragraph block: unsupported node {x.get('type') if isinstance(x, dict) else x!r}")
        n[0] += 1
        if n[0] > PARA_MAX_NODES:
            raise ValueError("paragraph block: too many nodes")
        for c in x.get("content") or []:
            walk(c, depth + 1)
    walk(doc, 0)
    if doc["type"] != "doc":
        raise ValueError("paragraph block content must be a doc")
    return doc


class ParagraphBlock(BaseModel):
    type: Literal["paragraph"]
    id: str | None = _Bid
    slot: str = SlotName
    content: dict

    @field_validator("content")
    @classmethod
    def _content(cls, v):
        return check_para(v)


Block = Annotated[Union[SnippetRefBlock, HeadingBlock, PageBreakBlock, TocBlock, ParagraphBlock], Field(discriminator="type")]


class VarDecl(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z_]\w*(\.\w+)?$", max_length=60)
    type: Literal["text", "date", "number", "list"] = "text"  # list = list of objects (rows)
    default: Any = None
    columns: list[Annotated[str, Field(pattern=r"^[A-Za-z_]\w*$", max_length=40)]] = Field(default=[], max_length=20)


def _check_value(t: str, v: Any) -> None:  # date values are stored as ISO by Variables._typed
    scalar = (str, int, float, bool, type(None))
    if v is None:
        return
    if t in ("text", "date") and not isinstance(v, str):
        raise ValueError("expected text")
    if t == "date" and v and parse_date(v) is None:
        raise ValueError("expected a date (DD.MM.YYYY or YYYY-MM-DD)")
    if t == "number" and (isinstance(v, bool) or not isinstance(v, (int, float))):
        raise ValueError("expected a number")
    if t == "list":
        if not isinstance(v, list) or len(v) > 1000:
            raise ValueError("expected a list of at most 1000 rows")
        for r in v:
            if not isinstance(r, dict) or len(r) > 20 or not all(
                    isinstance(k, str) and re.fullmatch(r"[A-Za-z_]\w*", k) and isinstance(x, scalar) for k, x in r.items()):
                raise ValueError("list rows must be objects with simple fields")


class Variables(BaseModel):
    declarations: list[VarDecl] = Field(default=[], max_length=200)
    values: dict[str, Any] = {}

    @model_validator(mode="after")
    def _typed(self):
        for d in self.declarations:
            for what, v in (("value", self.values.get(d.name)), ("default", d.default)):
                try:
                    _check_value(d.type, v)
                except ValueError as e:
                    raise ValueError(f"variable {d.name}: {what}: {e}")
            if d.type == "date":  # canonical storage: ISO, whatever was typed
                if self.values.get(d.name):
                    self.values[d.name] = parse_date(self.values[d.name]).isoformat()
                if d.default:
                    d.default = parse_date(d.default).isoformat()
        return self


class BaseCommitIn(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    override: bool = False  # commit although a newer version appeared since the draft started


class BaseRestoreIn(BaseModel):
    sha: str = Field(pattern=r"^[0-9a-f]{40}$")


class RevRow(BaseModel):
    version: str = Field(default="", max_length=64)
    date: str = Field(default="", max_length=32)
    author: str = Field(default="", max_length=120)
    message: str = Field(default="", max_length=500)
    sections: str = Field(default="", max_length=1000)  # what the revision changed (round 9)
    commit: str = Field(default="", max_length=500)  # commit subject


class LinkedDoc(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    doc_id: str | None = Field(default=None, max_length=100)
    version: str | None = Field(default=None, max_length=64)
    url: str | None = Field(default=None, max_length=500, pattern=r"^https?://")


class BaseRef(BaseModel):
    id: UUID
    version: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")  # null = pin the current head


class DocCreateIn(BaseModel):
    folder_id: UUID
    title: str = Field(min_length=1, max_length=200)
    slug: str | None = Field(default=None, pattern=SLUG)


class DocDraftIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    slug: str | None = Field(default=None, pattern=SLUG)
    base_document: BaseRef | None = None
    blocks: list[Block] = Field(default=[], max_length=1000)
    variables: Variables = Variables()
    links: list[LinkedDoc] = Field(default=[], max_length=200)
    revision_history: list[RevRow] | None = Field(default=None, max_length=500)  # null = auto-built from git


class DocOut(BaseModel):
    id: UUID
    folder_id: UUID
    slug: str
    title: str
    head_sha: str | None
    has_draft: bool = False


class ExportOut(BaseModel):
    id: UUID
    document_id: UUID
    format: str
    status: str
    error: str | None
    warnings: list
    pins: dict
    variables: dict
    created_at: datetime
    finished_at: datetime | None
    user_id: UUID | None


# ---- milestone 7: DOCX import ----
class ImportPut(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    content: dict  # TipTap doc; opaque nodes are re-validated server-side


Conflict = Literal["create_new_with_suffix", "update_existing", "skip"]


class SelectionIn(BaseModel):
    """Either `blocks` (the selected top-level blocks as the editor holds them: independent of whether the autosave
    reached the server) or a block index range [start, end) of the stored content."""
    folder_id: UUID
    title: str = Field(min_length=1, max_length=200)
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, gt=0)
    blocks: list[dict] | None = Field(default=None, min_length=1, max_length=5000)
    on_conflict: Conflict | None = None  # required when a snippet with this title/slug exists in the folder (else 409 name_exists)
    overwrite_draft: bool = False  # update_existing over the user's own unsaved draft

    @model_validator(mode="after")
    def _one_source(self):
        if self.blocks is None and (self.start is None or self.end is None):
            raise ValueError("give blocks or start+end")
        return self


class SplitIn(BaseModel):
    folder_id: UUID
    level: int | None = Field(default=None, ge=1, le=2)  # 1 = split at Heading 1, 2 = at Heading 1 and 2
    headings: list[int] | None = Field(default=None, max_length=1000)  # ... or exactly these heading blocks (indexes)
    titles: dict[int, str] | None = Field(default=None, max_length=1001)  # snippet title per heading index; -1 = preface
    preface: bool = False  # explicit headings only: blocks before the first one become a 'Front matter' snippet
    on_conflict: dict[int, Conflict] | None = None  # per section (heading index, -1 = preface) whose title exists in the folder
    overwrite_draft: bool = False


class UpdatePlanIn(BaseModel):
    folder_id: UUID
    headings: list[int] = Field(max_length=1000)  # the selected heading blocks: a snippet runs to the next selected one
    targets: dict[int, str] | None = None  # heading index -> snippet id | "new" | "skip" (user's choice over the auto match)


class UpdateItem(BaseModel):
    key: int
    action: Literal["update", "create", "skip"]
    snippet_id: UUID | None = None
    end: int | None = Field(default=None, gt=0)  # block index where this row's snippet ends (default: the next non-skipped heading)


class UpdateApplyIn(BaseModel):
    folder_id: UUID
    items: list[UpdateItem] = Field(max_length=1000)
    overwrite_draft: bool = False


class ConflictsIn(BaseModel):
    folder_id: UUID
    titles: list[str] = Field(max_length=1000)


class ImportBaseIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)  # new base document
    base_document_id: UUID | None = None  # ... or a new version of this one


class VarPick(BaseModel):
    value: str = Field(min_length=1, max_length=200)
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$", max_length=60)


class VarPicks(BaseModel):
    items: list[VarPick] = Field(max_length=300)


class ImportSnippetOut(SnippetOut):
    warnings: list[str] = []
    action: str = "created"  # created | updated (draft written over the existing snippet) | skipped
    base_sha: str | None = None
