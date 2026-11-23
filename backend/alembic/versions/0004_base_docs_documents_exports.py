"""base documents (+versions), documents (+drafts, refs), exports + RLS"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from app.rls import tenant_rls

revision = "0004"
down_revision = "0003"

org_fk = lambda: sa.Column("org_id", sa.Uuid, sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                           nullable=False, index=True)
fk = lambda col, tbl, **kw: sa.Column(col, sa.Uuid, sa.ForeignKey(f"{tbl}.id", ondelete=kw.pop("od", "CASCADE")), **kw)
now = lambda: sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)
TABLES = ("base_documents", "base_doc_versions", "documents", "document_drafts", "document_refs", "exports")


def upgrade():
    op.create_table("base_documents", sa.Column("id", sa.Uuid, primary_key=True), org_fk(),
                    sa.Column("name", sa.String(100), nullable=False), now())
    op.create_table("base_doc_versions", sa.Column("id", sa.Uuid, primary_key=True), org_fk(),
                    fk("base_document_id", "base_documents", nullable=False, index=True),
                    sa.Column("sha", sa.String(40), nullable=False), sa.Column("number", sa.Integer, nullable=False),
                    sa.Column("filename", sa.String(200), nullable=False), sa.Column("size", sa.Integer, nullable=False),
                    sa.Column("message", sa.String(500), nullable=False, server_default=""),
                    sa.Column("styles", JSONB, nullable=False), sa.Column("placeholders", JSONB, nullable=False),
                    fk("created_by", "users", od="SET NULL"), now(), sa.UniqueConstraint("base_document_id", "sha"))
    op.create_table("documents", sa.Column("id", sa.Uuid, primary_key=True), org_fk(),
                    fk("folder_id", "folders", nullable=False, index=True),
                    sa.Column("slug", sa.String(64), nullable=False), sa.Column("title", sa.String(200), nullable=False),
                    sa.Column("head_sha", sa.String(40)), now(), sa.UniqueConstraint("folder_id", "slug"))
    op.create_table("document_drafts", sa.Column("id", sa.Uuid, primary_key=True), org_fk(),
                    fk("document_id", "documents", nullable=False, index=True),
                    fk("user_id", "users", nullable=False),
                    sa.Column("title", sa.String(200), nullable=False), sa.Column("slug", sa.String(64), nullable=False),
                    sa.Column("body", JSONB, nullable=False), sa.Column("base_sha", sa.String(40)),
                    sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
                    sa.UniqueConstraint("document_id", "user_id"))
    op.create_table("document_refs", sa.Column("id", sa.Uuid, primary_key=True), org_fk(),
                    fk("document_id", "documents", nullable=False, index=True),
                    sa.Column("commit_sha", sa.String(40), nullable=False),
                    sa.Column("snippet_id", sa.Uuid, nullable=False, index=True),
                    sa.Column("folder_id", sa.Uuid, nullable=False),
                    sa.Column("pin_type", sa.String(8), nullable=False), sa.Column("pin_value", sa.String(64)))
    op.create_table("exports", sa.Column("id", sa.Uuid, primary_key=True), org_fk(),
                    fk("document_id", "documents", nullable=False, index=True), fk("user_id", "users", od="SET NULL"),
                    sa.Column("format", sa.String(8), nullable=False),
                    sa.Column("status", sa.String(8), nullable=False, server_default="queued"),
                    sa.Column("error", sa.Text), sa.Column("warnings", JSONB, nullable=False, server_default="[]"),
                    sa.Column("pins", JSONB, nullable=False), sa.Column("variables", JSONB, nullable=False),
                    sa.Column("spec", JSONB, nullable=False), sa.Column("sha256", sa.String(64)), now(),
                    sa.Column("finished_at", sa.DateTime(timezone=True)))
    for t in TABLES:
        for stmt in tenant_rls(t):
            op.execute(stmt)


def downgrade():
    for t in reversed(TABLES):
        op.drop_table(t)
