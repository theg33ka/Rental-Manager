from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from rental_manager.models import Lease, PaymentReceipt, RentCharge, UtilityBillLine
from rental_manager.services.billing import RENT_GENERATION_START, generate_rent_charges, money, update_rent_charge_status, update_utility_line_status

EPS = 0.009


def recalculate_lease_balances(session: Session, lease_id: int) -> None:
    charges = session.scalars(select(RentCharge).where(RentCharge.lease_id == lease_id)).all()
    lines = session.scalars(select(UtilityBillLine).where(UtilityBillLine.lease_id == lease_id)).all()

    charge_map = {charge.id: charge for charge in charges}
    line_map = {line.id: line for line in lines}

    for charge in charges:
        charge.ip_paid = 0.0
        charge.personal_paid = 0.0
    for line in lines:
        line.paid_amount = 0.0

    receipts = session.scalars(
        select(PaymentReceipt)
        .where(PaymentReceipt.lease_id == lease_id, PaymentReceipt.status == "accepted")
        .order_by(PaymentReceipt.paid_at, PaymentReceipt.id)
    ).all()
    for receipt in receipts:
        if receipt.utility_line_id and receipt.utility_line_id in line_map:
            line = line_map[receipt.utility_line_id]
            line.paid_amount = money(line.paid_amount + float(receipt.amount or 0))
            continue
        if receipt.rent_charge_id and receipt.rent_charge_id in charge_map:
            charge = charge_map[receipt.rent_charge_id]
            if normalized_rent_channel(receipt.channel) == "ip":
                charge.ip_paid = money(charge.ip_paid + float(receipt.amount or 0))
            else:
                charge.personal_paid = money(charge.personal_paid + float(receipt.amount or 0))

    for charge in charges:
        update_rent_charge_status(charge)
    for line in lines:
        update_utility_line_status(line)


def normalized_rent_channel(channel: str) -> str:
    return "ip" if channel == "expense_fund" else channel


def rent_charge_candidates(session: Session, lease_id: int, channel: str, cutoff: date | None = None) -> list[RentCharge]:
    cutoff = cutoff or RENT_GENERATION_START
    charges = session.scalars(
        select(RentCharge).where(RentCharge.lease_id == lease_id).order_by(RentCharge.due_date, RentCharge.id)
    ).all()
    for charge in charges:
        update_rent_charge_status(charge)
    return [charge for charge in charges if charge.due_date >= cutoff and rent_charge_debt(charge, channel) > EPS]


def utility_line_candidates(session: Session, lease_id: int) -> list[UtilityBillLine]:
    lines = session.scalars(
        select(UtilityBillLine)
        .where(UtilityBillLine.lease_id == lease_id)
        .order_by(UtilityBillLine.due_date, UtilityBillLine.id)
    ).all()
    for line in lines:
        update_utility_line_status(line)
    return [line for line in lines if utility_line_debt(line) > EPS]


def rent_charge_debt(charge: RentCharge, channel: str) -> float:
    channel = normalized_rent_channel(channel)
    if channel == "ip":
        return money(max(0.0, float(charge.ip_due or 0) - float(charge.ip_paid or 0)))
    return money(max(0.0, float(charge.personal_due or 0) - float(charge.personal_paid or 0)))


def utility_line_debt(line: UtilityBillLine) -> float:
    return money(max(0.0, float(line.total_amount or 0) - float(line.paid_amount or 0)))


def ensure_rent_debt_capacity(session: Session, lease: Lease, channel: str, amount: float, cutoff: date | None = None) -> list[RentCharge]:
    cutoff = cutoff or RENT_GENERATION_START
    horizon = date.today() + timedelta(days=365)
    for _ in range(3):
        generate_rent_charges(session, until=horizon)
        charges = rent_charge_candidates(session, lease.id, channel, cutoff)
        total = sum(rent_charge_debt(charge, channel) for charge in charges)
        if total + EPS >= amount:
            return charges
        horizon += timedelta(days=365)
    return rent_charge_candidates(session, lease.id, channel, cutoff)


def rent_channel_paid_amount(charge: RentCharge, channel: str) -> float:
    channel = normalized_rent_channel(channel)
    return money(float(charge.ip_paid or 0)) if channel == "ip" else money(float(charge.personal_paid or 0))


def charge_month_key(charge: RentCharge) -> tuple[int, int]:
    return charge.due_date.year, charge.due_date.month


def charge_for_payment_month(
    charges: list[RentCharge],
    paid_at: date | datetime | None,
) -> RentCharge | None:
    if not paid_at:
        return None
    paid_day = paid_at.date() if isinstance(paid_at, datetime) else paid_at
    for charge in charges:
        if charge.due_date.year != paid_day.year or charge.due_date.month != paid_day.month:
            continue
        return charge
    return None


def persisted_charge_for_payment_month(session: Session, lease_id: int, paid_at: date | datetime | None) -> RentCharge | None:
    if not paid_at:
        return None
    paid_day = paid_at.date() if isinstance(paid_at, datetime) else paid_at
    month_start = date(paid_day.year, paid_day.month, 1)
    if paid_day.month == 12:
        month_end = date(paid_day.year + 1, 1, 1) - timedelta(days=1)
    else:
        month_end = date(paid_day.year, paid_day.month + 1, 1) - timedelta(days=1)
    return session.scalar(
        select(RentCharge)
        .where(
            RentCharge.lease_id == lease_id,
            RentCharge.due_date >= month_start,
            RentCharge.due_date <= month_end,
        )
        .order_by(RentCharge.due_date, RentCharge.id)
        .limit(1)
    )


def describe_rent_allocation_decision(decision: dict[str, Any] | None) -> str:
    if not decision or not decision.get("target_due_date"):
        return ""
    target_due = decision["target_due_date"]
    target_label = f"{target_due:%m.%Y}"
    current_due = decision.get("current_due_date")
    current_label = f"{current_due:%m.%Y}" if current_due else ""
    mode = decision.get("mode")
    if mode == "current_month":
        return f"зачтено за {target_label}: в текущем месяце ещё не было зачтённого ИП-платежа"
    if mode == "nearest_past_debt":
        return f"зачтено за {target_label}: {current_label} уже тронут по ИП, закрываю ближайший старый долг"
    if mode == "advance":
        return f"зачтено за {target_label}: {current_label} уже тронут по ИП, долгов нет, ушло в аванс"
    return f"зачтено за {target_label}"


def reorder_ip_document_charges(
    session: Session,
    lease: Lease,
    charges: list[RentCharge],
    channel: str,
    paid_at: date | datetime | None,
    cutoff: date | None = None,
) -> tuple[list[RentCharge], dict[str, Any]]:
    if not paid_at:
        return charges, {"mode": "fifo", "target_due_date": charges[0].due_date if charges else None}
    paid_day = paid_at.date() if isinstance(paid_at, datetime) else paid_at
    current_charge = persisted_charge_for_payment_month(session, lease.id, paid_day) or charge_for_payment_month(charges, paid_day)
    if current_charge and cutoff and current_charge.due_date < cutoff:
        current_charge = None
    if current_charge and rent_channel_paid_amount(current_charge, channel) <= EPS and rent_charge_debt(current_charge, channel) > EPS:
        return [current_charge, *[charge for charge in charges if charge.id != current_charge.id]], {
            "mode": "current_month",
            "target_due_date": current_charge.due_date,
            "current_due_date": current_charge.due_date,
        }

    if current_charge and rent_channel_paid_amount(current_charge, channel) > EPS:
        current_key = charge_month_key(current_charge)
        past_debts = [charge for charge in charges if charge_month_key(charge) < current_key and rent_charge_debt(charge, channel) > EPS]
        if past_debts:
            target = max(past_debts, key=lambda charge: (charge.due_date, charge.id))
            return [target, *[charge for charge in charges if charge.id != target.id]], {
                "mode": "nearest_past_debt",
                "target_due_date": target.due_date,
                "current_due_date": current_charge.due_date,
            }
        future_debts = [charge for charge in charges if charge_month_key(charge) > current_key and rent_charge_debt(charge, channel) > EPS]
        if future_debts:
            target = min(future_debts, key=lambda charge: (charge.due_date, charge.id))
            return [target, *[charge for charge in charges if charge.id != target.id]], {
                "mode": "advance",
                "target_due_date": target.due_date,
                "current_due_date": current_charge.due_date,
            }

    return charges, {"mode": "fifo", "target_due_date": charges[0].due_date if charges else None}


def build_rent_plan(
    session: Session,
    lease: Lease,
    channel: str,
    amount: float,
    *,
    exact_only: bool,
    paid_at: date | datetime | None = None,
    prefer_document_month: bool = False,
    cutoff: date | None = None,
) -> tuple[list[tuple[RentCharge, float]], float, dict[str, Any] | None]:
    charges = ensure_rent_debt_capacity(session, lease, channel, amount, cutoff)
    decision: dict[str, Any] | None = None
    if prefer_document_month:
        if channel == "ip":
            charges, decision = reorder_ip_document_charges(session, lease, charges, channel, paid_at, cutoff)
        else:
            preferred = charge_for_payment_month(charges, paid_at)
            if preferred and rent_channel_paid_amount(preferred, channel) <= EPS and rent_charge_debt(preferred, channel) > EPS:
                charges = [preferred, *[charge for charge in charges if charge.id != preferred.id]]
                decision = {"mode": "current_month", "target_due_date": preferred.due_date, "current_due_date": preferred.due_date}
    plan, remaining = _build_plan(charges, lambda charge: rent_charge_debt(charge, channel), amount, exact_only=exact_only)
    if decision is None and plan:
        decision = {"mode": "fifo", "target_due_date": plan[0][0].due_date}
    return plan, remaining, decision


def build_utility_plan(
    session: Session,
    lease: Lease,
    amount: float,
    *,
    exact_only: bool,
) -> tuple[list[tuple[UtilityBillLine, float]], float]:
    lines = utility_line_candidates(session, lease.id)
    return _build_plan(lines, utility_line_debt, amount, exact_only=exact_only)


def create_rent_receipts(
    session: Session,
    lease: Lease,
    channel: str,
    amount: float,
    *,
    paid_at,
    source: str,
    status: str,
    recipient_name: str = "",
    recipient_details: str = "",
    notes: str = "",
    file_path: str = "",
    exact_only: bool = False,
    prefer_document_month: bool = False,
    cutoff: date | None = None,
) -> list[PaymentReceipt]:
    plan, remaining, _decision = build_rent_plan(
        session,
        lease,
        channel,
        amount,
        exact_only=exact_only,
        paid_at=paid_at,
        prefer_document_month=prefer_document_month,
        cutoff=cutoff,
    )
    if not plan or remaining > EPS:
        raise ValueError("Не удалось честно разложить платёж по аренде")
    receipts = [
        PaymentReceipt(
            lease_id=lease.id,
            rent_charge_id=charge.id,
            apartment_id=lease.apartment_id,
            amount=portion,
            channel=channel,
            paid_at=paid_at,
            source=source,
            status=status,
            recipient_name=recipient_name,
            recipient_details=recipient_details,
            file_path=file_path,
            notes=notes,
        )
        for charge, portion in plan
    ]
    session.add_all(receipts)
    session.flush()
    recalculate_lease_balances(session, lease.id)
    return receipts


def create_utility_receipts(
    session: Session,
    lease: Lease,
    amount: float,
    *,
    paid_at,
    source: str,
    status: str,
    recipient_name: str = "",
    recipient_details: str = "",
    notes: str = "",
    file_path: str = "",
    exact_only: bool = False,
) -> list[PaymentReceipt]:
    plan, remaining = build_utility_plan(session, lease, amount, exact_only=exact_only)
    if not plan or remaining > EPS:
        raise ValueError("Не удалось честно разложить платёж по коммуналке")
    receipts = [
        PaymentReceipt(
            lease_id=lease.id,
            utility_line_id=line.id,
            apartment_id=lease.apartment_id,
            amount=portion,
            channel="utilities",
            paid_at=paid_at,
            source=source,
            status=status,
            recipient_name=recipient_name,
            recipient_details=recipient_details,
            file_path=file_path,
            notes=notes,
        )
        for line, portion in plan
    ]
    session.add_all(receipts)
    session.flush()
    recalculate_lease_balances(session, lease.id)
    return receipts


def _build_plan(
    items: list[Any],
    debt_getter,
    amount: float,
    *,
    exact_only: bool,
) -> tuple[list[tuple[Any, float]], float]:
    remaining = money(amount)
    plan: list[tuple[Any, float]] = []
    for item in items:
        debt = money(float(debt_getter(item) or 0))
        if debt <= EPS:
            continue
        portion = min(remaining, debt)
        if portion > EPS:
            plan.append((item, money(portion)))
            remaining = money(remaining - portion)
        if remaining <= EPS:
            break
    if exact_only and remaining > EPS:
        return [], money(remaining)
    return plan, money(remaining)
