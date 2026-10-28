"""Ограниченные read-only проекции для диагностики финансов квартиры."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from rental_manager.models import (
    Apartment, Lease, PaymentReceipt, RentCharge, UtilityAdvanceLedger,
    UtilityAdvanceSetting, UtilityBill, UtilityBillLine, UtilityService,
)


def apartment_finance_snapshot(session: Session, apartment_id: int, limit: int = 100) -> dict[str, Any]:
    apartment = session.get(Apartment, apartment_id)
    if apartment is None:
        raise ValueError("Квартира не найдена")
    limit = min(500, max(1, limit))
    lease_ids = select(Lease.id).where(Lease.apartment_id == apartment_id)

    def rows(model, condition, fields):
        records = session.scalars(select(model).where(condition).order_by(model.id.desc()).limit(limit + 1)).all()
        return {
            "truncated": len(records) > limit,
            "rows": [{**{field: getattr(record, field) for field in fields},
                      **({"tenant": record.tenant.full_name} if isinstance(record, Lease) else {})}
                     for record in records[:limit]],
        }

    return {
        "apartment": {"id": apartment.id, "name": apartment.name, "object_id": apartment.object_id},
        "services": rows(UtilityService, UtilityService.object_id == apartment.object_id, ("id", "name", "kind")),
        "leases": rows(Lease, Lease.apartment_id == apartment_id,
                       ("id", "tenant_id", "start_date", "end_date", "active", "payment_day", "ip_amount", "personal_amount")),
        "rent_charges": rows(RentCharge, RentCharge.lease_id.in_(lease_ids),
                             ("id", "lease_id", "period_start", "period_end", "due_date", "ip_due", "personal_due", "ip_paid", "personal_paid", "status")),
        "utility_lines": rows(UtilityBillLine, UtilityBillLine.apartment_id == apartment_id,
                              ("id", "bill_id", "lease_id", "line_type", "total_amount", "paid_amount", "status", "metadata_json")),
        "utility_bills": rows(UtilityBill, UtilityBill.id.in_(
            select(UtilityBillLine.bill_id).where(UtilityBillLine.apartment_id == apartment_id)),
            ("id", "service_id", "bill_type", "period_start", "period_end", "status", "total_cost")),
        "payments": rows(PaymentReceipt, (PaymentReceipt.apartment_id == apartment_id) | PaymentReceipt.lease_id.in_(lease_ids),
                         ("id", "apartment_id", "lease_id", "rent_charge_id", "utility_line_id", "amount", "channel", "paid_at", "source", "status")),
        "advance_ledger": rows(UtilityAdvanceLedger, UtilityAdvanceLedger.apartment_id == apartment_id,
                               ("id", "lease_id", "utility_line_id", "payment_receipt_id", "period_start", "period_end", "amount", "kind")),
        "advance_settings": rows(UtilityAdvanceSetting, UtilityAdvanceSetting.apartment_id == apartment_id,
                                 ("id", "apartment_id", "amount_override")),
    }
