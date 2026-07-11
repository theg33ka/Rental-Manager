"""Create managed schema, security tables and measured indexes."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from rental_manager.database import Base
from rental_manager import models  # noqa: F401


revision = "20260711_01"
down_revision = None
branch_labels = None
depends_on = None


SCHEMA_ADDITIONS = {
    "utility_services": [
        sa.Column("provider_reading_due_day", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("provider_due_day", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("resident_due_days", sa.Integer(), nullable=False, server_default="7"),
    ],
    "utility_bills": [sa.Column("bill_type", sa.String(40), nullable=False, server_default="utility")],
    "utility_bill_lines": [
        sa.Column("line_type", sa.String(40), nullable=False, server_default="usage"),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
    ],
    "agent_action_proposals": [sa.Column("error_text", sa.Text(), nullable=False, server_default="")],
}

PERFORMANCE_INDEXES = {
    "ix_rm_leases_active_apartment": ("leases", ["active", "apartment_id"]),
    "ix_rm_rent_charges_lease_due": ("rent_charges", ["lease_id", "due_date"]),
    "ix_rm_rent_charges_due_status": ("rent_charges", ["due_date", "status"]),
    "ix_rm_payment_receipts_status_paid": ("payment_receipts", ["status", "paid_at"]),
    "ix_rm_payment_receipts_lease_paid": ("payment_receipts", ["lease_id", "paid_at"]),
    "ix_rm_message_logs_lease_created": ("message_logs", ["lease_id", "created_at"]),
    "ix_rm_meter_readings_meter_date": ("meter_readings", ["meter_id", "reading_date"]),
    "ix_rm_utility_bills_service_period": ("utility_bills", ["service_id", "period_start", "period_end"]),
    "ix_rm_utility_lines_lease_status": ("utility_bill_lines", ["lease_id", "status"]),
}


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())
    for table_name, columns in SCHEMA_ADDITIONS.items():
        if table_name not in table_names:
            continue
        existing = {column["name"] for column in inspector.get_columns(table_name)}
        for column in columns:
            if column.name not in existing:
                op.add_column(table_name, column)
    inspector = sa.inspect(bind)
    for index_name, (table_name, columns) in PERFORMANCE_INDEXES.items():
        if table_name not in table_names:
            continue
        existing_indexes = {index["name"] for index in inspector.get_indexes(table_name)}
        if index_name not in existing_indexes:
            op.create_index(index_name, table_name, columns, unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for index_name, (table_name, _) in reversed(list(PERFORMANCE_INDEXES.items())):
        if table_name in inspector.get_table_names() and index_name in {index["name"] for index in inspector.get_indexes(table_name)}:
            op.drop_index(index_name, table_name=table_name)
    for table_name in ("processed_telegram_updates", "panel_login_attempts", "panel_sessions"):
        if table_name in sa.inspect(bind).get_table_names():
            op.drop_table(table_name)
