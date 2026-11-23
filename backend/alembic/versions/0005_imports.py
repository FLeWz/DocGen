"""imports (uploaded DOCX under edit) + RLS"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from app.rls import tenant_rls

revision = "0005"
down_revision = "0004"


def upgrade():
    op.create_table(
        "imports", sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("org_id", sa.Uuid, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("title", sa.String(200), nullable=False), sa.Column("filename", sa.String(200), nullable=False),
        sa.Column("content", JSONB, nullable=False), sa.Column("warnings", JSONB, nullable=False, server_default="[]"),
        sa.Column("created_by", sa.Uuid, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    for stmt in tenant_rls("imports"):
        op.execute(stmt)


def downgrade():
    op.drop_table("imports")
