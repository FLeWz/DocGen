import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Role(str, enum.Enum):
    viewer = "viewer"
    editor = "editor"
    admin = "admin"
    owner = "owner"


RANK = {Role.viewer: 0, Role.editor: 1, Role.admin: 2, Role.owner: 3}


class Base(DeclarativeBase):
    pass


class OrgScoped:
    """Mixin for every tenant table. Also needs: an RLS policy in its migration
    (app/rls.py) and access only through OrgRepo (app/scoping.py)."""
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)


def _id():
    return mapped_column(primary_key=True, default=uuid.uuid4)


def _now():
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[uuid.UUID] = _id()
    name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = _now()


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _id()
    email: Mapped[str] = mapped_column(String(254), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = _now()


class Membership(OrgScoped, Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("org_id", "user_id"),)
    id: Mapped[uuid.UUID] = _id()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = _now()


class Invite(OrgScoped, Base):
    __tablename__ = "invites"
    id: Mapped[uuid.UUID] = _id()
    email: Mapped[str] = mapped_column(String(254))
    role: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # sha256; raw token only in the email
    invited_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()


class AuthSession(Base):
    __tablename__ = "sessions"
    id: Mapped[uuid.UUID] = _id()
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    active_org_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()


class Folder(OrgScoped, Base):
    """Backed by the bare repo /repos/{org_id}/{id}.git (path derived, never stored)."""
    __tablename__ = "folders"
    id: Mapped[uuid.UUID] = _id()
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("folders.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = _now()


class Snippet(OrgScoped, Base):
    """Index of git content: title/slug/head_sha mirror the last commit touching the snippet."""
    __tablename__ = "snippets"
    __table_args__ = (UniqueConstraint("folder_id", "slug"),)
    id: Mapped[uuid.UUID] = _id()
    folder_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("folders.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(200))
    head_sha: Mapped[str | None] = mapped_column(String(40))  # NULL until first commit
    created_at: Mapped[datetime] = _now()


class Draft(OrgScoped, Base):
    __tablename__ = "drafts"
    __table_args__ = (UniqueConstraint("snippet_id", "user_id"),)
    id: Mapped[uuid.UUID] = _id()
    snippet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("snippets.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(64))
    content: Mapped[dict] = mapped_column(JSONB)
    styles: Mapped[list] = mapped_column(JSONB, default=list)
    variables: Mapped[list] = mapped_column(JSONB, default=list)
    base_sha: Mapped[str | None] = mapped_column(String(40))  # snippet head_sha when the draft was started
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
