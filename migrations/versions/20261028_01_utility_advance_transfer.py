"""Replace the verified manual compensation with an audited advance transfer."""
from datetime import datetime
import json

from alembic import op
import sqlalchemy as sa

revision = "20261028_01"
down_revision = "20261002_01"
branch_labels = None
depends_on = None


def repair(bind):
    metadata = sa.MetaData()
    tables = {name: sa.Table(name, metadata, autoload_with=bind) for name in
              ("payment_receipts", "utility_advance_ledger", "utility_bill_lines", "manual_debts", "ai_action_logs")}
    receipts, ledger, lines, debts, audit = [tables[name] for name in tables]
    marker = "utility-transfer-compensation-20261028"
    if bind.scalar(sa.select(audit.c.id).where(audit.c.action_type == marker, audit.c.status == "executed").limit(1)):
        return

    def row(table, row_id):
        return bind.execute(sa.select(table).where(table.c.id == row_id)).mappings().first()

    payment, credit = row(receipts, 350), row(ledger, 3)
    debt, application, usage = row(debts, 8), row(ledger, 8), row(lines, 262)
    source_matches = credit and credit["lease_id"] == 29 and credit["apartment_id"] == 3 and credit["payment_receipt_id"] == 246
    target_matches = ((debt and debt["lease_id"] == 37 and debt["apartment_id"] == 3)
                      or (application and application["lease_id"] == 37 and application["utility_line_id"] == 262))
    if not source_matches or not target_matches:
        return

    def same(value, expected):
        return abs(float(value or 0) - expected) < 0.005

    valid = (
        payment and payment["lease_id"] == 41 and payment["apartment_id"] == 7
        and payment["channel"] == "utility_advance" and payment["status"] == "accepted"
        and same(payment["amount"], 2951) and "перенос аванса" in payment["notes"].lower()
        and credit["lease_id"] == 29 and credit["apartment_id"] == 3 and same(credit["amount"], 2951.08)
        and debt and debt["lease_id"] == 37 and debt["apartment_id"] == 3 and debt["kind"] == "utility" and debt["title"] == "Коррекция"
        and debt["active"] and same(debt["amount"], 2951) and same(debt["paid_amount"], 0)
        and application and application["lease_id"] == 37 and application["utility_line_id"] == 262
        and application["kind"] == "advance_applied" and same(application["amount"], -1047.05)
        and usage and usage["lease_id"] == 37 and usage["apartment_id"] == 3
        and same(usage["total_amount"], 1047.05) and same(usage["paid_amount"], 1047.05)
    )
    if not valid:
        raise RuntimeError("Advance transfer repair preconditions changed; review the financial records")
    if bind.scalar(sa.select(ledger.c.id).where(ledger.c.kind == "transfer_out", ledger.c.payment_receipt_id == 350).limit(1)):
        raise RuntimeError("Advance transfer repair has an incomplete prior transfer")

    now = datetime.now()
    before = {name: {key: value for key, value in record.items() if key in
                     {"id", "amount", "paid_amount", "active", "status", "kind", "lease_id", "utility_line_id", "source", "channel"}}
              for name, record in [("manual_debt", debt), ("application", application), ("usage", usage), ("payment", payment)]}
    bind.execute(ledger.update().where(ledger.c.id == 8).values(
        amount=0, kind="reversed_application", note=(application["note"] or "") + "; " + marker + ": reversed -1047.05"))
    bind.execute(ledger.insert().values(apartment_id=3, lease_id=29, payment_receipt_id=350,
                                       amount=-2951, kind="transfer_out", note=marker, created_at=now))
    bind.execute(ledger.insert().values(apartment_id=7, lease_id=41, payment_receipt_id=350,
                                       amount=2951, kind="transfer_in", note=marker, created_at=now))
    bind.execute(receipts.update().where(receipts.c.id == 350).values(
        source="utility_advance_transfer", status="transferred", notes=payment["notes"] + "; " + marker))
    paid = float(bind.scalar(sa.select(sa.func.coalesce(sa.func.sum(receipts.c.amount), 0)).where(
        receipts.c.utility_line_id == 262, receipts.c.lease_id == 37, receipts.c.status == "accepted")) or 0)
    bind.execute(lines.update().where(lines.c.id == 262).values(paid_amount=paid, status="issued" if paid < 1047.05 else "paid"))
    bind.execute(debts.update().where(debts.c.id == 8).values(
        active=False, status="cancelled", notes=(debt["notes"] or "") + "; " + marker, updated_at=now))
    bind.execute(audit.insert().values(actor_role="owner", action_type=marker, status="executed",
                                      payload_json=json.dumps({"before": before, "transfer_receipt_id": 350,
                                                               "source_credit_id": 3, "transfer_amount": 2951,
                                                               "source_remainder": 0.08}, ensure_ascii=False),
                                      note="Owner-approved replacement of manual compensation with advance transfer", created_at=now))


def upgrade():
    repair(op.get_bind())


def downgrade():
    raise RuntimeError("Financial repair is not automatically reversible; use a reviewed forward repair")
