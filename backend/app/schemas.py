from datetime import datetime
from uuid import UUID

from pydantic import BaseModel as _B, ConfigDict, EmailStr, Field


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
    snippet_ids: list[UUID] = Field(min_length=1, max_length=500)


class TagIn(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    sha: str | None = Field(default=None, max_length=64)  # default HEAD
