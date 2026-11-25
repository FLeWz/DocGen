"""commit display-name overrides (round 14) + RLS"""
import sqlalchemy as sa
from alembic import op

from app.rls import tenant_rls

revision = "0010"
down_revision = "0009"


def upgrade():
    op.create_table(
        "commit_names", sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("org_id", sa.Uuid, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("scope", sa.String(36), nullable=False), sa.Column("sha", sa.String(40), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("renamed_by", sa.Uuid, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("renamed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("org_id", "scope", "sha"))
    for stmt in tenant_rls("commit_names"):
        op.execute(stmt)


def downgrade():
    op.drop_table("commit_names")
