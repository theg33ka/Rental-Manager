"""Add cash payment methods and tenant handover requests."""

from alembic import op
import sqlalchemy as sa

revision = "20261001_01"
down_revision = "20260828_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {item["name"] for item in inspector.get_columns("payment_profiles")}
    for name in ("ip_payment_method", "personal_payment_method"):
        if name not in columns:
            op.add_column("payment_profiles", sa.Column(name, sa.String(20), nullable=False, server_default="transfer"))
    if "cash_payment_requests" not in inspector.get_table_names():
        op.create_table(
            "cash_payment_requests",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("token", sa.String(32), nullable=False, unique=True),
            sa.Column("rent_charge_id", sa.Integer(), sa.ForeignKey("rent_charges.id", ondelete="CASCADE"), nullable=False),
            sa.Column("tenant_chat_id", sa.String(80), nullable=False),
            sa.Column("ip_amount", sa.Float(), nullable=False, server_default="0"),
            sa.Column("personal_amount", sa.Float(), nullable=False, server_default="0"),
            sa.Column("snapshot_json", sa.Text(), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="offered"),
            sa.Column("proposal_id", sa.Integer(), sa.ForeignKey("agent_action_proposals.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("claimed_at", sa.DateTime(), nullable=True),
            sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_cash_payment_requests_rent_charge_id", "cash_payment_requests", ["rent_charge_id"])


def downgrade() -> None:
    op.drop_table("cash_payment_requests")
    with op.batch_alter_table("payment_profiles") as batch:
        batch.drop_column("personal_payment_method")
        batch.drop_column("ip_payment_method")
