"""folders, snippets (git index), drafts + RLS"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from app.rls import tenant_rls

revision = "0002"
down_revision = "0001"

org_fk = lambda: sa.Column("org_id", sa.Uuid, sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                           nullable=False, index=True)


def upgrade():
    op.create_table("folders", sa.Column("id", sa.Uuid, primary_key=True), org_fk(),
                    sa.Column("parent_id", sa.Uuid, sa.ForeignKey("folders.id", ondelete="CASCADE"), index=True),
                    sa.Column("name", sa.String(100), nullable=False),
                    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_table("snippets", sa.Column("id", sa.Uuid, primary_key=True), org_fk(),
                    sa.Column("folder_id", sa.Uuid, sa.ForeignKey("folders.id", ondelete="CASCADE"),
                              nullable=False, index=True),
                    sa.Column("slug", sa.String(64), nullable=False),
                    sa.Column("title", sa.String(200), nullable=False),
                    sa.Column("head_sha", sa.String(40)),
                    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
                    sa.UniqueConstraint("folder_id", "slug"))
    op.create_table("drafts", sa.Column("id", sa.Uuid, primary_key=True), org_fk(),
                    sa.Column("snippet_id", sa.Uuid, sa.ForeignKey("snippets.id", ondelete="CASCADE"),
                              nullable=False, index=True),
                    sa.Column("user_id", sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("title", sa.String(200), nullable=False),
                    sa.Column("slug", sa.String(64), nullable=False),
                    sa.Column("content", JSONB, nullable=False),
                    sa.Column("styles", JSONB, nullable=False, server_default="[]"),
                    sa.Column("variables", JSONB, nullable=False, server_default="[]"),
                    sa.Column("base_sha", sa.String(40)),
                    sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
                    sa.UniqueConstraint("snippet_id", "user_id"))
    for t in ("folders", "snippets", "drafts"):
        for stmt in tenant_rls(t):
            op.execute(stmt)


def downgrade():
    for t in ("drafts", "snippets", "folders"):
        op.drop_table(t)
