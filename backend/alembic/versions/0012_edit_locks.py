"""round 16: edit locks, lock requests, notifications, one shared draft per entity (+ backups of the merged-away ones)"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from app.rls import tenant_rls

revision = "0012"
down_revision = "0011"


def _org():
    return sa.Column("org_id", sa.Uuid, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)


def _user(name, nullable=False):
    return sa.Column(name, sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE" if not nullable else "SET NULL"), nullable=nullable)


def _ts(name, nullable=False):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade():
    op.create_table("edit_locks", sa.Column("id", sa.Uuid, primary_key=True), _org(),
                    sa.Column("entity_type", sa.String(8), nullable=False), sa.Column("entity_id", sa.Uuid, nullable=False),
                    _user("holder_id"), _ts("since"), _ts("heartbeat_at"),
                    sa.UniqueConstraint("org_id", "entity_type", "entity_id"))
    op.create_table("lock_requests", sa.Column("id", sa.Uuid, primary_key=True), _org(),
                    sa.Column("entity_type", sa.String(8), nullable=False), sa.Column("entity_id", sa.Uuid, nullable=False),
                    _user("requester_id"), _user("holder_id"), sa.Column("status", sa.String(10), nullable=False),
                    _ts("created_at"), _ts("expires_at"), _ts("resolved_at", True))
    op.create_index("lock_requests_one_pending", "lock_requests", ["org_id", "entity_type", "entity_id"], unique=True,
                    postgresql_where=sa.text("status = 'pending'"))
    op.create_index("lock_requests_cooldown", "lock_requests", ["org_id", "entity_id", "requester_id", "created_at"])
    op.create_table("notifications", sa.Column("id", sa.Uuid, primary_key=True), _org(), _user("user_id"),
                    sa.Column("kind", sa.String(24), nullable=False), sa.Column("payload", JSONB, nullable=False),
                    _ts("read_at", True), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("notifications_user", "notifications", ["user_id", "created_at"])
    op.create_table("draft_backups", sa.Column("id", sa.Uuid, primary_key=True), _org(),
                    sa.Column("entity_type", sa.String(8), nullable=False), sa.Column("entity_id", sa.Uuid, nullable=False, index=True),
                    _user("user_id", True), sa.Column("payload", JSONB, nullable=False), _ts("draft_updated_at", True),
                    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    for t in ("edit_locks", "lock_requests", "notifications", "draft_backups"):
        for stmt in tenant_rls(t):
            op.execute(stmt)

    # the background sweeper has no org context and RLS hides every row: this exposes only the org ids to look at
    op.execute("CREATE FUNCTION app_lock_orgs() RETURNS TABLE(org_id uuid) LANGUAGE sql STABLE SECURITY DEFINER "
               "SET search_path = public AS $$ SELECT DISTINCT r.org_id FROM lock_requests r WHERE r.status = 'pending' $$")

    # one shared draft per entity: the newest wins, the others go to draft_backups
    for table, fk, kind in (("drafts", "snippet_id", "snippet"), ("document_drafts", "document_id", "document")):
        op.execute(f"""INSERT INTO draft_backups (id, org_id, entity_type, entity_id, user_id, payload, draft_updated_at)
            SELECT gen_random_uuid(), org_id, '{kind}', {fk}, user_id, to_jsonb(d) - 'org_id', updated_at FROM (
              SELECT *, row_number() OVER (PARTITION BY {fk} ORDER BY updated_at DESC, id) AS rn FROM {table}) d WHERE rn > 1""")
        op.execute(f"""DELETE FROM {table} WHERE id IN (SELECT id FROM (
              SELECT id, row_number() OVER (PARTITION BY {fk} ORDER BY updated_at DESC, id) AS rn FROM {table}) d WHERE rn > 1)""")
        op.drop_constraint(f"{table}_{fk}_user_id_key", table)
        op.create_unique_constraint(f"{table}_{fk}_key", table, [fk])
        op.add_column(table, sa.Column("contributors", JSONB, nullable=False, server_default="[]"))
        op.add_column(table, sa.Column("transfers", JSONB, nullable=False, server_default="[]"))
        # the surviving draft's author is its first contributor
        op.execute(f"""UPDATE {table} t SET contributors = jsonb_build_array(jsonb_build_object(
            'id', u.id::text, 'name', u.name, 'email', u.email, 'at', to_char(t.updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')))
            FROM users u WHERE u.id = t.user_id""")


def downgrade():
    op.execute("DROP FUNCTION app_lock_orgs()")
    for table, fk in (("drafts", "snippet_id"), ("document_drafts", "document_id")):
        op.drop_column(table, "transfers")
        op.drop_column(table, "contributors")
        op.drop_constraint(f"{table}_{fk}_key", table)
        op.create_unique_constraint(f"{table}_{fk}_user_id_key", table, [fk, "user_id"])
    for t in ("draft_backups", "notifications", "lock_requests", "edit_locks"):
        op.drop_table(t)
