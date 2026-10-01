from __future__ import annotations

import json
import secrets

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from rental_manager.models import AgentActionProposal, CashPaymentRequest, PaymentReceipt, RentCharge, utc_now
from rental_manager.services.payment_profiles import effective_payment_settings
from rental_manager.services.payment_allocation import recalculate_lease_balances


def cash_amounts(charge: RentCharge) -> dict[str, float]:
    settings = effective_payment_settings(charge.lease.apartment, {})
    return {
        channel: round(max(0.0, float(getattr(charge, f"{channel}_due")) - float(getattr(charge, f"{channel}_paid"))), 2)
        if settings.get(f"{channel}_payment_method") == "cash" else 0.0
        for channel in ("ip", "personal")
    }


def cash_snapshot(charge: RentCharge) -> str:
    amounts = cash_amounts(charge)
    return json.dumps({
        "lease_id": charge.lease_id,
        "tenant_id": charge.lease.tenant_id,
        "due_date": charge.due_date.isoformat(),
        "period_start": charge.period_start.isoformat(),
        "period_end": charge.period_end.isoformat(),
        "amounts": amounts,
        "paid": {channel: round(float(getattr(charge, f"{channel}_paid")), 2) for channel in amounts if amounts[channel] > 0},
    }, sort_keys=True)


def cash_request_valid(request: CashPaymentRequest) -> bool:
    charge = request.rent_charge
    return bool(
        charge and charge.lease.active and charge.lease.apartment.active
        and request.ip_amount + request.personal_amount > 0
        and request.snapshot_json == cash_snapshot(charge)
        and {"ip": request.ip_amount, "personal": request.personal_amount} == cash_amounts(charge)
    )


def offer_cash_payment(session: Session, charge: RentCharge, chat_id: str) -> CashPaymentRequest | None:
    amounts = cash_amounts(charge)
    if sum(amounts.values()) <= 0:
        return None
    snapshot = cash_snapshot(charge)
    existing = session.scalars(select(CashPaymentRequest).where(
        CashPaymentRequest.rent_charge_id == charge.id,
        CashPaymentRequest.status.in_(["offered", "pending"]),
    ).order_by(CashPaymentRequest.id.desc())).all()
    for item in existing:
        if item.status == "pending":
            proposal = session.get(AgentActionProposal, item.proposal_id) if item.proposal_id else None
            if proposal and (proposal.status not in {"pending", "approved"} or (proposal.expires_at and proposal.expires_at < utc_now())):
                item.status = "closed"
                continue
        if item.snapshot_json == snapshot and item.tenant_chat_id == str(chat_id):
            return item
        item.status = "stale"
    item = CashPaymentRequest(
        token=secrets.token_hex(12), rent_charge_id=charge.id, tenant_chat_id=str(chat_id),
        ip_amount=amounts["ip"], personal_amount=amounts["personal"], snapshot_json=snapshot,
    )
    session.add(item)
    session.flush()
    return item


def confirm_cash_payment(session: Session, request_id: int, proposal: AgentActionProposal) -> list[PaymentReceipt]:
    request = session.get(CashPaymentRequest, request_id)
    if not request or request.proposal_id != proposal.id or proposal.status != "approved" or not proposal.confirmed_by:
        raise HTTPException(409, "Нет подтверждённого владельцем запроса на наличный платёж")
    if request.status == "accepted":
        return []
    if proposal.expires_at and proposal.expires_at < utc_now():
        raise HTTPException(409, "Срок подтверждения истёк")
    # Захватываем запрос до проверки начисления, чтобы повторный callback не создал второй платёж.
    changed = session.execute(update(CashPaymentRequest).where(
        CashPaymentRequest.id == request.id, CashPaymentRequest.status == "pending",
    ).values(status="confirming")).rowcount
    if changed != 1:
        raise HTTPException(409, "Запрос уже обработан или устарел")
    charge = session.scalar(select(RentCharge).where(RentCharge.id == request.rent_charge_id).with_for_update().execution_options(populate_existing=True))
    if not charge or not cash_request_valid(request):
        request.status = "stale"
        raise HTTPException(409, "Сумма, договор или способ оплаты изменились. Нужен новый запрос жильца")
    receipts = []
    for channel in ("ip", "personal"):
        amount = getattr(request, f"{channel}_amount")
        if amount <= 0:
            continue
        receipt = PaymentReceipt(
            lease_id=charge.lease_id, rent_charge_id=charge.id, apartment_id=charge.lease.apartment_id,
            amount=amount, channel=channel, source="cash_confirmed", status="accepted",
            paid_at=request.claimed_at or utc_now(),
            notes=f"Наличные получены. Запрос №{request.id}; подтверждение владельца №{proposal.id} ({proposal.confirmed_by}).",
        )
        session.add(receipt)
        receipts.append(receipt)
    session.flush()
    recalculate_lease_balances(session, charge.lease_id)
    request.status = "accepted"
    request.confirmed_at = utc_now()
    session.flush()
    return receipts
