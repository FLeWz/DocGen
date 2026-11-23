"""styles (org style set, seeded for existing orgs) + images + RLS"""
import sqlalchemy as sa
from alembic import op

from app.rls import tenant_rls
from app.styles import DEFAULTS

revision = "0003"
down_revision = "0002"

org_fk = lambda: sa.Column("org_id", sa.Uuid, sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                           nullable=False, index=True)


def upgrade():
    op.create_table(
        "styles", sa.Column("id", sa.Uuid, primary_key=True), org_fk(),
        sa.Column("style_id", sa.String(40), nullable=False), sa.Column("name", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False), sa.Column("level", sa.Integer),
        sa.Column("color", sa.String(7)), sa.Column("font_size", sa.Float), sa.Column("font_family", sa.String(60)),
        sa.Column("numbering", sa.String(8), nullable=False, server_default="none"),
        sa.Column("space_before", sa.Float, nullable=False, server_default="0"),
        sa.Column("space_after", sa.Float, nullable=False, server_default="0"),
        sa.Column("bold", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("italic", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("builtin", sa.Boolean, nullable=False, server_default="false"),
        sa.UniqueConstraint("org_id", "style_id"))
    op.create_table(
        "images", sa.Column("id", sa.Uuid, primary_key=True), org_fk(),
        sa.Column("ext", sa.String(4), nullable=False), sa.Column("mime", sa.String(20), nullable=False),
        sa.Column("size", sa.Integer, nullable=False), sa.Column("width", sa.Integer, nullable=False),
        sa.Column("height", sa.Integer, nullable=False),
        sa.Column("created_by", sa.Uuid, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    for t in ("styles", "images"):
        for stmt in tenant_rls(t):
            op.execute(stmt)
    # existing orgs get the built-in set (migrations run as the table owner, so RLS does not interfere)
    conn = op.get_bind()
    cols = ["style_id", "name", "kind", "level", "color", "font_size", "font_family", "numbering",
            "space_before", "space_after", "bold", "italic"]
    ins = sa.text(f"insert into styles (id, org_id, builtin, {', '.join(cols)}) "
                  f"values (gen_random_uuid(), :org, true, {', '.join(':' + c for c in cols)})")
    for (org,) in conn.execute(sa.text("select id from organizations")).all():
        for d in DEFAULTS:
            conn.execute(ins, {"org": org, **{c: d.get(c, "none" if c == "numbering" else 0 if c.startswith("space") else
                                                      False if c in ("bold", "italic") else None) for c in cols}})


def downgrade():
    op.drop_table("images")
    op.drop_table("styles")
