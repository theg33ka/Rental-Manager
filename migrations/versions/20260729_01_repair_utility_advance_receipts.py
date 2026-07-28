"""Repair payments linked to utility advance lines.

Revision ID: 20260729_01
Revises: 20260720_01
Create Date: 2026-07-29
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260729_01"
down_revision: str | None = "20260720_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    receipts = sa.table(
        "payment_receipts",
        sa.column("id", sa.Integer()),
        sa.column("lease_id", sa.Integer()),
        sa.column("apartment_id", sa.Integer()),
        sa.column("utility_line_id", sa.Integer()),
        sa.column("amount", sa.Float()),
        sa.column("channel", sa.String()),
        sa.column("status", sa.String()),
        sa.column("notes", sa.Text()),
    )
    lines = sa.table(
        "utility_bill_lines",
        sa.column("id", sa.Integer()),
        sa.column("bill_id", sa.Integer()),
        sa.column("lease_id", sa.Integer()),
        sa.column("apartment_id", sa.Integer()),
        sa.column("line_type", sa.String()),
    )
    bills = sa.table(
        "utility_bills",
        sa.column("id", sa.Integer()),
        sa.column("period_start", sa.Date()),
        sa.column("period_end", sa.Date()),
    )
    ledger = sa.table(
        "utility_advance_ledger",
        sa.column("id", sa.Integer()),
        sa.column("apartment_id", sa.Integer()),
        sa.column("lease_id", sa.Integer()),
        sa.column("utility_line_id", sa.Integer()),
        sa.column("payment_receipt_id", sa.Integer()),
        sa.column("period_start", sa.Date()),
        sa.column("period_end", sa.Date()),
        sa.column("amount", sa.Float()),
        sa.column("kind", sa.String()),
        sa.column("note", sa.Text()),
        sa.column("created_at", sa.DateTime()),
    )

    advance_receipts = bind.execute(
        sa.select(
            receipts.c.id,
            receipts.c.amount,
            receipts.c.notes,
            lines.c.lease_id,
            lines.c.apartment_id,
            lines.c.id.label("utility_line_id"),
            bills.c.period_start,
            bills.c.period_end,
        )
        .select_from(
            receipts.join(lines, receipts.c.utility_line_id == lines.c.id).join(
                bills,
                lines.c.bill_id == bills.c.id,
            )
        )
        .where(
            receipts.c.status == "accepted",
            lines.c.line_type == "advance",
        )
    ).mappings().all()

    for receipt in advance_receipts:
        bind.execute(
            receipts.update()
            .where(receipts.c.id == receipt["id"])
            .values(channel="utility_advance")
        )
        ledger_values = {
            "apartment_id": receipt["apartment_id"],
            "lease_id": receipt["lease_id"],
            "utility_line_id": receipt["utility_line_id"],
            "payment_receipt_id": receipt["id"],
            "period_start": receipt["period_start"],
            "period_end": receipt["period_end"],
            "amount": round(float(receipt["amount"] or 0), 2),
            "kind": "advance_payment",
            "note": receipt["notes"] or "оплата аванса коммуналки",
        }
        existing_id = bind.scalar(
            sa.select(ledger.c.id)
            .where(
                ledger.c.payment_receipt_id == receipt["id"],
                ledger.c.kind == "advance_payment",
            )
            .limit(1)
        )
        if existing_id:
            bind.execute(
                ledger.update()
                .where(ledger.c.id == existing_id)
                .values(**ledger_values)
            )
        else:
            bind.execute(
                ledger.insert().values(
                    **ledger_values,
                    created_at=sa.func.now(),
                )
            )


def downgrade() -> None:
    pass
