"""snippets remember the import heading they were made from (round 12): later imports of the same manual match by it"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"


def upgrade():
    op.add_column("snippets", sa.Column("source", postgresql.JSONB(), nullable=True))


def downgrade():
    op.drop_column("snippets", "source")
