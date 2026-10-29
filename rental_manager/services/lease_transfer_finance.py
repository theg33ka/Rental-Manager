"""Перенос существующего аванса без создания поступления денег."""
from __future__ import annotations

import math
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from rental_manager.models import Lease, PaymentReceipt, UtilityAdvanceLedger
from rental_manager.services.billing import money
from rental_manager.services.utility_advances import advance_owner_lease_ids


def advance_balance(session: Session, lease: Lease) -> float:
    owners = advance_owner_lease_ids(session, lease.apartment_id, lease.id)
    ledger = session.scalar(select(func.coalesce(func.sum(UtilityAdvanceLedger.amount), 0)).where(
        UtilityAdvanceLedger.apartment_id == lease.apartment_id,
        UtilityAdvanceLedger.lease_id.in_(owners),
    )) or 0
    legacy = session.scalar(select(func.coalesce(func.sum(PaymentReceipt.amount), 0)).where(
        PaymentReceipt.apartment_id == lease.apartment_id, PaymentReceipt.lease_id.in_(owners),
        PaymentReceipt.status == "accepted", PaymentReceipt.channel == "utility_advance",
        PaymentReceipt.utility_line_id.is_(None), PaymentReceipt.rent_charge_id.is_(None),
    )) or 0
    result = float(ledger) + float(legacy)
    if not math.isfinite(result) or result < -0.009:
        raise ValueError("Несогласованный остаток коммунального аванса")
    return money(result)


def transfer_advance(session: Session, source: Lease, target: Lease) -> float:
    # Блокируем оба договора в одном порядке перед проверкой остатка.
    session.scalars(select(Lease).where(Lease.id.in_([source.id, target.id])).order_by(Lease.id).with_for_update()).all()
    if (source.tenant_id != target.tenant_id or source.apartment_id == target.apartment_id
            or source.end_date != target.start_date - timedelta(days=1)):
        raise ValueError("Договоры не образуют переезд одного жильца")
    marker = f"lease_transfer:{source.id}:{target.id}"
    existing = session.scalars(select(UtilityAdvanceLedger).where(UtilityAdvanceLedger.note == marker)).all()
    if existing:
        outgoing = [r for r in existing if r.kind == "transfer_out" and r.lease_id == source.id
                    and r.apartment_id == source.apartment_id and r.amount < 0]
        incoming = [r for r in existing if r.kind == "transfer_in" and r.lease_id == target.id
                    and r.apartment_id == target.apartment_id and r.amount > 0]
        if len(existing) != 2 or len(outgoing) != 1 or len(incoming) != 1 or money(sum(r.amount for r in existing)) != 0:
            raise ValueError("Несогласованная пара переноса аванса")
        return 0
    amount = advance_balance(session, source)
    if amount <= 0:
        return 0
    session.add_all([
        UtilityAdvanceLedger(apartment_id=source.apartment_id, lease_id=source.id,
                             amount=-amount, kind="transfer_out", note=marker),
        UtilityAdvanceLedger(apartment_id=target.apartment_id, lease_id=target.id,
                             amount=amount, kind="transfer_in", note=marker),
    ])
    session.flush()
    return amount
