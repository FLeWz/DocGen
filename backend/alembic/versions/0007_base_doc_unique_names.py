"""base document names unique per org (case-insensitive, trimmed)"""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"


def upgrade():
    c = op.get_bind()
    c.execute(sa.text("UPDATE base_documents SET name = btrim(name) WHERE name <> btrim(name)"))
    rows = c.execute(sa.text("SELECT id, org_id, name FROM base_documents ORDER BY created_at, id")).all()
    seen: set[tuple] = set()
    for id_, org, name in rows:  # later duplicates become 'X (2)', 'X (3)'; nothing is deleted
        n, new = 1, name
        while (org, new.lower()) in seen:
            n += 1
            suffix = f" ({n})"
            new = name[:100 - len(suffix)] + suffix
        seen.add((org, new.lower()))
        if new != name:
            c.execute(sa.text("UPDATE base_documents SET name = :n WHERE id = :i"), {"n": new, "i": id_})
    op.execute("CREATE UNIQUE INDEX uq_base_documents_org_name ON base_documents (org_id, lower(name))")


def downgrade():
    op.execute("DROP INDEX uq_base_documents_org_name")
