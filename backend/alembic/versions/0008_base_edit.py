"""imports can be an editing session of a base document (round 9): which base document, which version it started from"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"


def upgrade():
    op.add_column("imports", sa.Column("base_document_id", sa.Uuid(), sa.ForeignKey("base_documents.id", ondelete="CASCADE"), nullable=True))
    op.add_column("imports", sa.Column("base_sha", sa.String(40), nullable=True))
    op.add_column("imports", sa.Column("base_fp", postgresql.JSONB(), nullable=True))  # block fingerprints at the start: the diff summary
    op.create_index("ix_imports_base_document_id", "imports", ["base_document_id"])


def downgrade():
    op.drop_index("ix_imports_base_document_id", "imports")
    for c in ("base_fp", "base_sha", "base_document_id"):
        op.drop_column("imports", c)
