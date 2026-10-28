from __future__ import annotations

import json

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from rental_manager.models import AiActionLog, MessageLog, PaymentReceipt, UtilityAdvanceLedger, UtilityBill
from rental_manager.services.billing import money
from rental_manager.services.payment_allocation import recalculate_lease_balances


def delete_bill_preserving_payments(session: Session, bill_id: int) -> dict:
    bill = session.scalar(select(UtilityBill).where(UtilityBill.id == bill_id).with_for_update())
    if bill is None:
        raise HTTPException(404, "Коммунальный счёт не найден.")
    lines = {line.id: line for line in bill.lines}
    receipts = session.scalars(select(PaymentReceipt).where(PaymentReceipt.utility_line_id.in_(lines))).all()
    entries = session.scalars(select(UtilityAdvanceLedger).where(UtilityAdvanceLedger.utility_line_id.in_(lines))).all()
    accepted = [receipt for receipt in receipts if receipt.status == "accepted" and receipt.amount > 0.009]
    has_payments = accepted or any(line.paid_amount > 0.009 for line in lines.values()) or any(entry.amount for entry in entries)
    if has_payments and (bill.status != "draft" or bill.bill_type == "advance"):
        raise HTTPException(400, "Нельзя удалить оплаченный счёт. Удаление с возвратом денег в аванс доступно только для черновика фактической коммуналки.")

    accounted = {line_id: 0.0 for line_id in lines}
    for receipt in accepted:
        line = lines[receipt.utility_line_id]
        if (not line.lease_id or receipt.lease_id != line.lease_id
                or receipt.apartment_id != line.apartment_id or receipt.rent_charge_id
                or line.line_type == "advance"):
            raise HTTPException(409, "Привязки оплаты требуют проверки перед удалением черновика.")
        accounted[line.id] += receipt.amount
    for entry in entries:
        line = lines[entry.utility_line_id]
        if entry.amount and (entry.kind != "advance_applied" or entry.amount > 0
                             or not line.lease_id or entry.lease_id != line.lease_id
                             or entry.apartment_id != line.apartment_id):
            raise HTTPException(409, "Журнал авансов требует проверки перед удалением черновика.")
        accounted[line.id] -= min(0, entry.amount)
    if any(abs(money(accounted[line.id]) - money(line.paid_amount)) > 0.009 for line in lines.values()):
        raise HTTPException(409, "Суммы оплат не совпадают с журналом. Сначала проверьте оплаты черновика.")

    returned = money(sum(accounted.values()))
    for receipt in receipts:
        receipt.utility_line_id = None
        if receipt in accepted:
            receipt.channel = "utility_advance"
            receipt.notes = "; ".join(filter(None, [receipt.notes, f"Возврат в аванс после удаления черновика №{bill.id}"]))
    for entry in entries:
        entry.utility_line_id = None
        if entry.amount < 0:
            session.add(UtilityAdvanceLedger(
                apartment_id=entry.apartment_id, lease_id=entry.lease_id,
                amount=-entry.amount, kind="deletion_return",
                period_start=entry.period_start, period_end=entry.period_end,
                note=f"Возврат зачёта №{entry.id} после удаления черновика №{bill.id}",
            ))
    for log in session.scalars(select(MessageLog).where(MessageLog.utility_line_id.in_(lines))).all():
        log.utility_line_id = None
    result = {"ok": True, "bill_id": bill.id, "deleted_status": bill.status, "returned_to_advance": returned}
    session.add(AiActionLog(
        actor_role="owner", action_type="delete_utility_bill", status="executed",
        payload_json=json.dumps({**result, "receipt_ids": [r.id for r in accepted],
                                 "ledger_ids": [e.id for e in entries], "line_ids": list(lines)}),
        note="Удаление коммунального счёта с сохранением платежей и журнала авансов",
    ))
    lease_ids = {line.lease_id for line in lines.values() if line.lease_id}
    session.delete(bill)
    session.flush()
    for lease_id in sorted(lease_ids):
        recalculate_lease_balances(session, lease_id)
    session.commit()
    return result
