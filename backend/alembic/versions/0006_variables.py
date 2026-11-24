"""declared variables of base document versions and imports"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006"
down_revision = "0005"


def upgrade():
    for t in ("base_doc_versions", "imports"):
        op.add_column(t, sa.Column("variables", JSONB, nullable=False, server_default="[]"))


def downgrade():
    for t in ("base_doc_versions", "imports"):
        op.drop_column(t, "variables")
