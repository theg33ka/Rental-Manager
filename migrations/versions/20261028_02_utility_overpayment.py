"""Recover a verified ignored payment and correct its tenant's allocations."""
from datetime import datetime, timezone
from decimal import Decimal
import json

from alembic import op
import sqlalchemy as sa

revision = "20261028_02"
down_revision = "20261028_01"
branch_labels = None
depends_on = None


def repair(bind):
    metadata = sa.MetaData()
    tables = {name: sa.Table(name, metadata, autoload_with=bind) for name in
              ("payment_receipts", "rent_charges", "utility_bill_lines", "leases", "ai_action_logs")}
    receipts, charges, lines, leases, audit = [tables[name] for name in tables]
    marker = "utility-overpayment-reconcile-20261028"
    if bind.scalar(sa.select(audit.c.id).where(audit.c.action_type == marker, audit.c.status == "executed").limit(1)):
        return

    def row(table, row_id):
        return bind.execute(sa.select(table).where(table.c.id == row_id)).mappings().first()

    source = row(receipts, 353)
    split = row(receipts, 380)
    lease = row(leases, 37)
    if not source or not split or not lease or source["lease_id"] != 37 or source["apartment_id"] != 3 or lease["tenant_id"] != 32:
        return
    duplicate, parent, rent_part, utility_payment = [row(receipts, row_id) for row_id in (354, 377, 379, 376)]
    charge, old_usage, current_usage = row(charges, 986), row(lines, 262), row(lines, 280)

    def same(value, expected):
        return Decimal(str(value or 0)) == Decimal(str(expected))

    def detail(receipt):
        try:
            return json.loads(receipt["recipient_details"] or "{}") if receipt else {}
        except (ValueError, TypeError):
            raise RuntimeError("Payment evidence is invalid; review the financial records") from None

    source_details = detail(source)
    source_key = source_details.get("receipt_number")
    split_key = detail(split).get("receipt_number")
    utility_key = detail(utility_payment).get("receipt_number")
    evidence_matches = (
        source_key and split_key and utility_key and len({source_key, split_key, utility_key}) == 3
        and source_key == detail(duplicate).get("receipt_number")
        and split_key == detail(parent).get("receipt_number") == detail(rent_part).get("receipt_number")
        and same(source_details.get("amount"), 2951) and same(detail(split).get("amount"), 5000)
        and same(detail(utility_payment).get("amount"), 2951)
    )
    valid = (
        evidence_matches and source["status"] == "ignored" and source["source"] == "telegram"
        and source["utility_line_id"] is None and source["rent_charge_id"] is None and same(source["amount"], 2951)
        and split["lease_id"] == 37 and split["apartment_id"] == 3 and split["status"] == "accepted"
        and split["utility_line_id"] == 262 and split["rent_charge_id"] is None and same(split["amount"], 2951)
        and duplicate and duplicate["status"] == "duplicate" and same(duplicate["amount"], 2951)
        and parent and parent["status"] == "moderated" and same(parent["amount"], 5000)
        and rent_part and rent_part["status"] == "accepted" and rent_part["rent_charge_id"] == 986
        and rent_part["channel"] == "personal" and same(rent_part["amount"], 2049)
        and utility_payment and utility_payment["status"] == "accepted" and utility_payment["utility_line_id"] == 280
        and same(utility_payment["amount"], 2951)
        and charge and charge["lease_id"] == 37 and same(charge["ip_due"], 20000) and same(charge["personal_due"], 5000)
        and same(charge["ip_paid"], 20000) and same(charge["personal_paid"], 2049)
        and old_usage and old_usage["lease_id"] == 37 and old_usage["apartment_id"] == 3
        and same(old_usage["total_amount"], 1047.05) and same(old_usage["paid_amount"], 2951)
        and current_usage and current_usage["lease_id"] == 37 and current_usage["apartment_id"] == 3
        and same(current_usage["total_amount"], 3660) and same(current_usage["paid_amount"], 2951)
    )
    linked_old = bind.execute(sa.select(receipts.c.id).where(
        receipts.c.utility_line_id == 262, receipts.c.status == "accepted")).scalars().all()
    linked_current = bind.execute(sa.select(receipts.c.id).where(
        receipts.c.utility_line_id == 280, receipts.c.status == "accepted")).scalars().all()
    if not valid or linked_old != [380] or linked_current != [376]:
        raise RuntimeError("Utility overpayment repair preconditions changed; review the financial records")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    before = {"ignored_receipt_id": 353, "ignored_amount": 2951, "reassigned_receipt_id": 380,
              "rent_personal_paid": 2049, "old_utility_paid": 2951, "current_utility_paid": 2951}
    bind.execute(receipts.update().where(receipts.c.id == 380).values(
        utility_line_id=None, rent_charge_id=986, channel="personal", notes=(split["notes"] or "") + "; " + marker))
    bind.execute(receipts.update().where(receipts.c.id == 353).values(
        status="moderated", notes=(source["notes"] or "") + "; " + marker + ": allocated once"))

    old_amount = Decimal(str(old_usage["total_amount"]))
    current_remainder = Decimal(str(current_usage["total_amount"])) - Decimal(str(utility_payment["amount"]))
    advance = Decimal(str(source["amount"])) - old_amount - current_remainder
    if advance != Decimal("1194.95"):
        raise RuntimeError("Utility overpayment conservation check failed")
    allocations = [(262, old_amount, "utilities"), (280, current_remainder, "utilities"), (None, advance, "utility_advance")]
    child_ids = []
    for line_id, amount, channel in allocations:
        result = bind.execute(receipts.insert().values(
            lease_id=37, apartment_id=3, utility_line_id=line_id, amount=float(amount), channel=channel,
            paid_at=source["paid_at"], source=source["source"], status="accepted", is_expense=False,
            recipient_name=source["recipient_name"], recipient_details=source["recipient_details"], file_path=source["file_path"],
            notes=marker + ": allocated from receipt 353", created_at=now))
        child_ids.append(result.inserted_primary_key[0])

    personal_paid = float(bind.scalar(sa.select(sa.func.coalesce(sa.func.sum(receipts.c.amount), 0)).where(
        receipts.c.rent_charge_id == 986, receipts.c.channel == "personal", receipts.c.status == "accepted")) or 0)
    if personal_paid != 5000:
        raise RuntimeError("Rent conservation check failed")
    bind.execute(charges.update().where(charges.c.id == 986).values(personal_paid=personal_paid, status="paid"))
    for line in (old_usage, current_usage):
        paid = float(bind.scalar(sa.select(sa.func.coalesce(sa.func.sum(receipts.c.amount), 0)).where(
            receipts.c.utility_line_id == line["id"], receipts.c.status == "accepted")) or 0)
        if not same(paid, line["total_amount"]):
            raise RuntimeError("Utility conservation check failed")
        bind.execute(lines.update().where(lines.c.id == line["id"]).values(paid_amount=paid, status="paid"))
    bind.execute(audit.insert().values(actor_role="owner", lease_id=37, action_type=marker, status="executed",
                                      payload_json=json.dumps({"before": before, "child_receipt_ids": child_ids,
                                                               "utility_advance": float(advance), "duplicate_receipt_id": 354}),
                                      note="Owner-authorized payment reconciliation; no new cash or duplicate receipt credit", created_at=now))


def upgrade():
    repair(op.get_bind())


def downgrade():
    raise RuntimeError("Financial allocations require a reviewed forward repair")
