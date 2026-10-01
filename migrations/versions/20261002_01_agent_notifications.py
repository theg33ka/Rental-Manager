"""Add durable manager notification outbox; preserve all business history."""
from alembic import op
import sqlalchemy as sa

revision = "20261002_01"
down_revision = "20261001_01"
branch_labels = None
depends_on = None


def upgrade():
    if "agent_notifications" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table("agent_notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("dedupe_key", sa.String(200), nullable=False, unique=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("operational_cases.id"), nullable=True),
        sa.Column("channel", sa.String(30), nullable=False),
        sa.Column("importance", sa.String(30), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("remote_message_id", sa.String(80), nullable=False, server_default=""),
        sa.Column("error", sa.String(200), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(), nullable=True),
        sa.Column("read_at", sa.DateTime(), nullable=True))
    op.create_index("ix_agent_notifications_case_id", "agent_notifications", ["case_id"])
    op.create_index("ix_agent_notifications_status", "agent_notifications", ["status"])


def downgrade():
    raise RuntimeError("Notification history is retained. Roll back application or use a forward repair.")
