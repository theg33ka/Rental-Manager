"""Add expense flag to rental payment receipts.

Revision ID: 20260720_01
Revises: 20260715_01
Create Date: 2026-07-20
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260720_01"
down_revision: str | None = "20260715_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {column["name"] for column in sa.inspect(bind).get_columns("payment_receipts")}
    if "is_expense" not in existing:
        op.add_column(
            "payment_receipts",
            sa.Column("is_expense", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing = {column["name"] for column in sa.inspect(bind).get_columns("payment_receipts")}
    if "is_expense" in existing:
        op.drop_column("payment_receipts", "is_expense")
