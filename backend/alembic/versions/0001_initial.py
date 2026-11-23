"""users, orgs, memberships, invites, sessions + RLS"""
import sqlalchemy as sa
from alembic import op

from app.rls import tenant_rls

revision = "0001"
down_revision = None

ts = lambda: sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)
org_fk = lambda: sa.Column("org_id", sa.Uuid, sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                           nullable=False, index=True)


def upgrade():
    op.create_table("organizations", sa.Column("id", sa.Uuid, primary_key=True),
                    sa.Column("name", sa.String(100), nullable=False), ts())
    op.create_table("users", sa.Column("id", sa.Uuid, primary_key=True),
                    sa.Column("email", sa.String(254), nullable=False, unique=True),
                    sa.Column("name", sa.String(100), nullable=False),
                    sa.Column("password_hash", sa.String(200), nullable=False), ts())
    op.create_table("memberships", sa.Column("id", sa.Uuid, primary_key=True), org_fk(),
                    sa.Column("user_id", sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"),
                              nullable=False, index=True),
                    sa.Column("role", sa.String(16), nullable=False), ts(),
                    sa.CheckConstraint("role in ('owner','admin','editor','viewer')"),
                    sa.UniqueConstraint("org_id", "user_id"))
    op.create_table("invites", sa.Column("id", sa.Uuid, primary_key=True), org_fk(),
                    sa.Column("email", sa.String(254), nullable=False),
                    sa.Column("role", sa.String(16), nullable=False),
                    sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
                    sa.Column("invited_by", sa.Uuid, sa.ForeignKey("users.id", ondelete="SET NULL")),
                    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
                    sa.Column("accepted_at", sa.DateTime(timezone=True)),
                    sa.Column("revoked_at", sa.DateTime(timezone=True)), ts(),
                    sa.CheckConstraint("role in ('owner','admin','editor','viewer')"))
    op.create_table("sessions", sa.Column("id", sa.Uuid, primary_key=True),
                    sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
                    sa.Column("user_id", sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"),
                              nullable=False, index=True),
                    sa.Column("active_org_id", sa.Uuid, sa.ForeignKey("organizations.id", ondelete="SET NULL")),
                    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False), ts())

    op.execute("CREATE FUNCTION app_uuid(k text) RETURNS uuid LANGUAGE sql STABLE "
               "AS $$ SELECT NULLIF(current_setting(k, true), '')::uuid $$")
    op.execute("CREATE FUNCTION app_text(k text) RETURNS text LANGUAGE sql STABLE "
               "AS $$ SELECT NULLIF(current_setting(k, true), '') $$")

    op.execute("ALTER TABLE organizations ENABLE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY org_select ON organizations FOR SELECT USING (id = app_uuid('app.org_id') OR "
               "EXISTS (SELECT 1 FROM memberships m WHERE m.org_id = organizations.id "
               "AND m.user_id = app_uuid('app.user_id')))")
    op.execute("CREATE POLICY org_insert ON organizations FOR INSERT WITH CHECK (id = app_uuid('app.org_id'))")
    op.execute("CREATE POLICY org_update ON organizations FOR UPDATE USING (id = app_uuid('app.org_id'))")
    op.execute("CREATE POLICY org_delete ON organizations FOR DELETE USING (id = app_uuid('app.org_id'))")

    # memberships: org wall for writes; reads also allow a user's own rows (org switcher / login)
    op.execute("ALTER TABLE memberships ENABLE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY m_select ON memberships FOR SELECT USING "
               "(org_id = app_uuid('app.org_id') OR user_id = app_uuid('app.user_id'))")
    for cmd, clause in (("INSERT", "WITH CHECK"), ("UPDATE", "USING"), ("DELETE", "USING")):
        op.execute(f"CREATE POLICY m_{cmd.lower()} ON memberships FOR {cmd} {clause} (org_id = app_uuid('app.org_id'))")

    # invites: org wall, plus the single row whose token hash the request presents (public accept flow)
    op.execute("ALTER TABLE invites ENABLE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY tenant_isolation ON invites USING "
               "(org_id = app_uuid('app.org_id') OR token_hash = app_text('app.invite_hash'))")

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO docgen_app")
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO docgen_app")


def downgrade():
    for t in ("sessions", "invites", "memberships", "users", "organizations"):
        op.drop_table(t)
    op.execute("DROP FUNCTION app_text(text)")
    op.execute("DROP FUNCTION app_uuid(text)")
