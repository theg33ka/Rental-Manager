"""Узкая сверка переезда с проверкой состояния и сохранением исходных записей."""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from rental_manager.models import AiActionLog, Apartment, Expense, Lease, PaymentReceipt, RentCharge, UtilityAdvanceLedger, UtilityBill, UtilityBillLine
from rental_manager.services.billing import money, update_rent_charge_status, update_utility_line_status
from rental_manager.services.expense_rent_credit import CREDIT_SOURCE
from rental_manager.services.lease_transfer_finance import advance_balance, transfer_advance
from rental_manager.services.payment_allocation import recalculate_lease_balances


def repair_state(session: Session, arguments: dict[str, Any]) -> str:
    ids = [int(arguments['source_lease_id']), int(arguments['target_lease_id'])]
    leases = session.scalars(select(Lease).where(Lease.id.in_(ids)).with_for_update()).all()
    if len(leases) != 2:
        raise ValueError("Нужны два разных существующих договора")
    owner_ids = session.scalars(select(Lease.id).where(
        Lease.tenant_id.in_([lease.tenant_id for lease in leases]),
        Lease.apartment_id.in_([lease.apartment_id for lease in leases]),
    )).all()
    records = []
    conditions: list[tuple[Any, Any]] = [
        (Lease, Lease.id.in_(owner_ids)),
        (Expense, Expense.id == int(arguments['expense_id'])),
        (Apartment, Apartment.id == int(arguments['funding_apartment_id'])),
        (PaymentReceipt, PaymentReceipt.lease_id.in_(owner_ids)),
        (RentCharge, RentCharge.lease_id.in_(ids)),
        (UtilityBillLine, UtilityBillLine.lease_id.in_(ids)),
        (UtilityBill, UtilityBill.id.in_(select(UtilityBillLine.bill_id).where(UtilityBillLine.lease_id.in_(ids)))),
        (UtilityAdvanceLedger, UtilityAdvanceLedger.lease_id.in_(owner_ids)),
    ]
    for model, condition in conditions:
        for row in session.scalars(select(model).where(condition).order_by(model.id).with_for_update().execution_options(populate_existing=True)).all():
            records.append([model.__tablename__, {c.name: getattr(row, c.name) for c in model.__table__.columns}])
    return hashlib.sha256(json.dumps(records, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def repair_transfer(session: Session, arguments: dict[str, Any], *, sync_advance, apply_bill) -> dict[str, Any]:
    source = session.get(Lease, int(arguments['source_lease_id']))
    target = session.get(Lease, int(arguments['target_lease_id']))
    expense = session.get(Expense, int(arguments['expense_id']))
    funding = session.get(Apartment, int(arguments['funding_apartment_id']))
    if not source or not target or not expense or not funding:
        raise ValueError("Не найдены договоры, расход или источник денег")
    marker = f"transfer_repair:{source.id}:{target.id}:{expense.id}"
    if session.scalar(select(AiActionLog.id).where(AiActionLog.action_type == 'repair_transfer_finance', AiActionLog.note == marker)):
        return {"already_applied": True}
    if repair_state(session, arguments) != arguments.get('expected_fingerprint'):
        raise ValueError("Данные изменились после preview; нужна повторная сверка")
    if (source.tenant_id != target.tenant_id or source.apartment_id == target.apartment_id
            or source.end_date != target.start_date - timedelta(days=1) or source.active):
        raise ValueError("Договоры не образуют закрытый переезд одного жильца")
    if (expense.apartment_id != source.apartment_id or funding.id == source.apartment_id
            or expense.source_funds != 'rental_budget'):
        raise ValueError("Расход не соответствует подтверждённому источнику денег")
    credits = session.scalars(select(PaymentReceipt).where(PaymentReceipt.source == f'{CREDIT_SOURCE}{expense.id}')).all()
    if (not credits or money(sum(r.amount for r in credits)) != money(expense.amount)
            or any(r.lease_id != source.id or r.status != 'accepted' or not r.is_expense for r in credits)):
        raise ValueError("Зачёт расхода изменён или имеет другие связи")
    pairs = arguments.get('duplicate_pairs', [])
    if not isinstance(pairs, list):
        raise ValueError("Нужны пары исходной и повторной записи")
    seen: set[int] = set()
    duplicates = []
    for pair in pairs:
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError("Неверная пара повторных записей")
        original, duplicate = [session.get(PaymentReceipt, int(i)) for i in pair]
        if not original or not duplicate or original.id == duplicate.id or seen.intersection(pair):
            raise ValueError("Пары повторных записей пересекаются или не найдены")
        seen.update(pair)
        fields = ('lease_id', 'apartment_id', 'utility_line_id', 'amount', 'paid_at', 'channel', 'source', 'status')
        if (any(getattr(original, f) != getattr(duplicate, f) for f in fields)
                or original.lease_id != source.id or original.apartment_id != source.apartment_id
                or not original.utility_line_id or original.rent_charge_id or duplicate.rent_charge_id
                or original.status != 'accepted' or original.channel not in {'utilities', 'utility_advance'}):
            raise ValueError("Повторная запись отличается от согласованного платежа")
        duplicates.append(duplicate)
    future = session.scalars(select(RentCharge).where(RentCharge.lease_id == source.id, RentCharge.due_date > source.end_date)).all()
    credit_ids = {r.id for r in credits}
    for charge in future:
        if any(r.status == 'accepted' and r.id not in credit_ids for r in charge.receipts):
            raise ValueError("В будущем периоде есть реальная оплата; нужна отдельная сверка")
    before = advance_balance(session, source) + advance_balance(session, target)
    for receipt in credits:
        receipt.status = 'cancelled'
        receipt.notes = '; '.join(filter(None, [receipt.notes, marker]))
    expense.notes = '; '.join(filter(None, [expense.notes, f'RENT_CREDIT_REVERSED:{expense.id}; источник бюджета: {funding.name}; {marker}']))
    for receipt in duplicates:
        receipt.status = 'duplicate'
        receipt.notes = '; '.join(filter(None, [receipt.notes, marker]))
    session.flush()
    source_receipts = session.scalars(select(PaymentReceipt).where(PaymentReceipt.lease_id == source.id)).all()
    for receipt in source_receipts:
        sync_advance(session, receipt)
    session.flush()
    for lease in (source, target):
        recalculate_lease_balances(session, lease.id)
    source_lines = session.scalars(select(UtilityBillLine).join(UtilityBill).where(
        UtilityBillLine.lease_id == source.id, UtilityBillLine.line_type != 'advance',
        UtilityBillLine.status != 'cancelled', UtilityBill.status == 'issued',
    )).all()
    if any(abs(money(line.total_amount - line.paid_amount)) > 0.009 for line in source_lines):
        raise ValueError("Фактические счета старой квартиры требуют отдельной сверки")
    for charge in future:
        charge.ip_due = 0
        charge.personal_due = 0
        update_rent_charge_status(charge)
    session.flush()
    available = advance_balance(session, source) + advance_balance(session, target)
    transferred = transfer_advance(session, source, target)
    session.flush()
    bills = session.scalars(select(UtilityBill).where(UtilityBill.status == 'issued', UtilityBill.id.in_(
        select(UtilityBillLine.bill_id).where(UtilityBillLine.lease_id == target.id))).order_by(UtilityBill.period_end, UtilityBill.id)).all()
    applied = sum(apply_bill(session, bill) for bill in bills)
    after = advance_balance(session, source) + advance_balance(session, target)
    if money(after + applied) != money(available):
        raise ValueError("Нарушено сохранение суммы аванса")
    advance_requests = []
    forecast_cover = advance_balance(session, target)
    for line in session.scalars(select(UtilityBillLine).join(UtilityBill).where(
        UtilityBillLine.lease_id == target.id, UtilityBillLine.line_type == 'advance',
        UtilityBillLine.status != 'cancelled', UtilityBill.status == 'issued',
    ).order_by(UtilityBill.period_end, UtilityBillLine.id)).all():
        outstanding = money(max(0, line.total_amount - line.paid_amount))
        covered = money(min(outstanding, forecast_cover))
        if covered <= 0:
            continue
        previous = line.total_amount
        line.total_amount = money(line.total_amount - covered)
        line.note = '; '.join(filter(None, [line.note, f'{marker}; прогноз покрыт существующим авансом: {covered}']))
        if line.total_amount <= 0 and line.paid_amount <= 0:
            line.status = 'cancelled'
        else:
            update_utility_line_status(line)
        forecast_cover = money(forecast_cover - covered)
        advance_requests.append({'id': line.id, 'before': previous, 'after': line.total_amount})
    result = {'cancelled_credit': money(expense.amount), 'duplicate_receipts': [r.id for r in duplicates],
              'advance_before': before, 'advance_normalized': available, 'transferred': transferred,
              'applied_to_usage': money(applied), 'advance_after': after, 'advance_requests': advance_requests}
    session.add(AiActionLog(action_type='repair_transfer_finance', actor_role='owner', status='executed',
                            note=marker, payload_json=json.dumps(result, ensure_ascii=False)))
    session.flush()
    return result
