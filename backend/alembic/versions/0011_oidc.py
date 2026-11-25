"""OpenID Connect: identities, password-less users, id_token on sessions, RLS-bypassing lookups for first login"""
import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"


def upgrade():
    op.alter_column("users", "password_hash", existing_type=sa.String(200), nullable=True)
    op.add_column("sessions", sa.Column("id_token", sa.Text))  # kept for RP-initiated logout (id_token_hint)
    # (issuer, sub) is the stable key of an SSO user; users stay global like `users` itself (no org_id, no RLS)
    op.create_table(
        "identities", sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("user_id", sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("issuer", sa.String(500), nullable=False), sa.Column("sub", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("issuer", "sub"))
    # Before the first login the user has no org context, so RLS hides orgs and invites. These two
    # SECURITY DEFINER functions (owned by the migration role, which bypasses RLS) expose exactly what the
    # OIDC callback needs and nothing else.
    op.execute("CREATE FUNCTION app_find_org(n text) RETURNS uuid LANGUAGE sql STABLE SECURITY DEFINER "
               "SET search_path = public AS $$ SELECT id FROM organizations WHERE name = n ORDER BY created_at LIMIT 1 $$")
    op.execute("CREATE FUNCTION app_pending_invites(e text) RETURNS TABLE(id uuid, org_id uuid, role text) "
               "LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$ "
               "SELECT i.id, i.org_id, i.role::text FROM invites i WHERE i.email = e AND i.accepted_at IS NULL "
               "AND i.revoked_at IS NULL AND i.expires_at > now() $$")


def downgrade():
    op.execute("DROP FUNCTION app_pending_invites(text)")
    op.execute("DROP FUNCTION app_find_org(text)")
    op.drop_table("identities")
    op.drop_column("sessions", "id_token")
    op.alter_column("users", "password_hash", existing_type=sa.String(200), nullable=False)
