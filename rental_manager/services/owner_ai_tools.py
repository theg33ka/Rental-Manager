from __future__ import annotations

from datetime import date, timedelta
import json
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from rental_manager.models import (
    AiConversation,
    AiMessage,
    Apartment,
    Expense,
    Lease,
    ManualDebt,
    PaymentReceipt,
    PaymentSituation,
    RentalObject,
    Tenant,
    UtilityBill,
    UtilityBillLine,
    UtilityService,
)
from rental_manager.services.owner_operations import owner_operation_catalog


READ_TOOL_NAMES = (
    "get_overdue_payments",
    "get_contracts_ending_soon",
    "get_rentals_summary",
    "get_tenants_summary",
    "get_properties_summary",
    "get_tenant_details",
    "get_property_details",
    "get_owner_chat_context",
    "search_rentals",
)

PROPOSE_TOOL_NAMES = (
    "propose_owner_operation",
)


def get_overdue_payments(dashboard: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for category in ("rent_overdue", "rent_partial", "utility_overdue", "manual_debts"):
        for item in dashboard.get(category, []):
            result.append({"category": category, **dict(item)})
    return result


def get_contracts_ending_soon(
    session: Session,
    days: int = 30,
    *,
    today: date | None = None,
) -> list[dict[str, Any]]:
    current = today or date.today()
    until = current + timedelta(days=min(365, max(1, int(days))))
    leases = session.scalars(
        select(Lease)
        .options(
            joinedload(Lease.tenant),
            joinedload(Lease.apartment).joinedload(Apartment.object),
        )
        .where(
            Lease.active.is_(True),
            Lease.end_date.is_not(None),
            Lease.end_date >= current,
            Lease.end_date <= until,
        )
        .order_by(Lease.end_date, Lease.id)
    ).all()
    return [
        {
            "lease_id": lease.id,
            "tenant_id": lease.tenant_id,
            "tenant": lease.tenant.full_name,
            "property_id": lease.apartment.object_id,
            "property": lease.apartment.object.name,
            "apartment": lease.apartment.name,
            "end_date": lease.end_date.isoformat() if lease.end_date else None,
        }
        for lease in leases
    ]


def get_rentals_summary(session: Session) -> dict[str, int]:
    return {
        "active": int(session.scalar(select(func.count(Lease.id)).where(Lease.active.is_(True))) or 0),
        "inactive": int(session.scalar(select(func.count(Lease.id)).where(Lease.active.is_(False))) or 0),
    }


def get_tenants_summary(session: Session) -> dict[str, int]:
    return {
        "active": int(session.scalar(select(func.count(Tenant.id)).where(Tenant.active.is_(True))) or 0),
        "total": int(session.scalar(select(func.count(Tenant.id))) or 0),
    }


def get_properties_summary(session: Session) -> dict[str, int]:
    return {
        "properties": int(session.scalar(select(func.count(RentalObject.id))) or 0),
        "apartments": int(session.scalar(select(func.count(Apartment.id))) or 0),
        "active_apartments": int(
            session.scalar(select(func.count(Apartment.id)).where(Apartment.active.is_(True))) or 0
        ),
    }


def get_tenant_details(session: Session, tenant_id: int) -> dict[str, Any] | None:
    tenant = session.get(Tenant, int(tenant_id))
    if tenant is None:
        return None
    leases = session.scalars(
        select(Lease)
        .options(joinedload(Lease.apartment).joinedload(Apartment.object))
        .where(Lease.tenant_id == tenant.id)
        .order_by(Lease.active.desc(), Lease.start_date.desc(), Lease.id.desc())
    ).all()
    return {
        "tenant_id": tenant.id,
        "full_name": tenant.full_name,
        "phone": tenant.phone,
        "telegram": tenant.telegram,
        "active": tenant.active,
        "leases": [
            {
                "lease_id": lease.id,
                "property": lease.apartment.object.name,
                "apartment": lease.apartment.name,
                "active": lease.active,
                "start_date": lease.start_date.isoformat(),
                "end_date": lease.end_date.isoformat() if lease.end_date else None,
            }
            for lease in leases
        ],
    }


def get_property_details(session: Session, property_id: int) -> dict[str, Any] | None:
    rental_object = session.scalar(
        select(RentalObject)
        .options(joinedload(RentalObject.apartments))
        .where(RentalObject.id == int(property_id))
    )
    if rental_object is None:
        return None
    return {
        "property_id": rental_object.id,
        "name": rental_object.name,
        "apartments": [
            {
                "apartment_id": apartment.id,
                "name": apartment.name,
                "active": apartment.active,
            }
            for apartment in rental_object.apartments
        ],
    }


def get_owner_chat_context(session: Session, chat_id: int | str, limit: int = 12) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(AiMessage)
        .join(AiConversation, AiMessage.conversation_id == AiConversation.id)
        .where(
            AiConversation.role == "owner",
            AiConversation.chat_id == str(chat_id),
            AiMessage.role.in_(["user", "assistant"]),
        )
        .order_by(AiMessage.created_at.desc(), AiMessage.id.desc())
        .limit(min(50, max(1, int(limit))))
    ).all()
    return [
        {
            "role": row.role,
            "text": (row.text or "")[:2400],
            "created_at": row.created_at.isoformat(),
        }
        for row in reversed(rows)
    ]


def search_rentals(session: Session, query: str, limit: int = 20) -> list[dict[str, Any]]:
    value = (query or "").strip()
    if not value:
        return []
    pattern = f"%{value}%"
    leases = session.scalars(
        select(Lease)
        .join(Lease.tenant)
        .join(Lease.apartment)
        .join(Apartment.object)
        .options(
            joinedload(Lease.tenant),
            joinedload(Lease.apartment).joinedload(Apartment.object),
        )
        .where(
            or_(
                Tenant.full_name.ilike(pattern),
                Tenant.phone.ilike(pattern),
                Tenant.telegram.ilike(pattern),
                Apartment.name.ilike(pattern),
                RentalObject.name.ilike(pattern),
            )
        )
        .order_by(Lease.active.desc(), Lease.id.desc())
        .limit(min(50, max(1, int(limit))))
    ).all()
    return [
        {
            "lease_id": lease.id,
            "tenant_id": lease.tenant_id,
            "tenant": lease.tenant.full_name,
            "property_id": lease.apartment.object_id,
            "property": lease.apartment.object.name,
            "apartment": lease.apartment.name,
            "active": lease.active,
        }
        for lease in leases
    ]


def build_owner_read_tools_context(
    session: Session,
    dashboard: dict[str, Any],
    chat_id: int | str,
) -> str:
    objects = session.scalars(
        select(RentalObject)
        .options(joinedload(RentalObject.apartments), joinedload(RentalObject.services))
        .order_by(RentalObject.name, RentalObject.id)
    ).unique().all()
    services = session.scalars(
        select(UtilityService)
        .options(joinedload(UtilityService.object), joinedload(UtilityService.meters))
        .order_by(UtilityService.object_id, UtilityService.name, UtilityService.id)
    ).unique().all()
    bills = session.scalars(
        select(UtilityBill)
        .options(joinedload(UtilityBill.service).joinedload(UtilityService.object))
        .order_by(UtilityBill.created_at.desc(), UtilityBill.id.desc())
        .limit(30)
    ).all()
    manual_debts = session.scalars(
        select(ManualDebt)
        .where(ManualDebt.active.is_(True))
        .order_by(ManualDebt.due_date.desc(), ManualDebt.id.desc())
        .limit(40)
    ).all()
    receipts = session.scalars(
        select(PaymentReceipt)
        .order_by(PaymentReceipt.paid_at.desc(), PaymentReceipt.id.desc())
        .limit(40)
    ).all()
    expenses = session.scalars(
        select(Expense)
        .order_by(Expense.expense_date.desc(), Expense.id.desc())
        .limit(30)
    ).all()
    utility_lines = session.scalars(
        select(UtilityBillLine)
        .where(UtilityBillLine.status.in_(["issued", "partial", "overdue"]))
        .order_by(UtilityBillLine.due_date.desc(), UtilityBillLine.id.desc())
        .limit(40)
    ).all()
    payment_situations = session.scalars(
        select(PaymentSituation)
        .order_by(PaymentSituation.updated_at.desc(), PaymentSituation.id.desc())
        .limit(80)
    ).all()
    snapshot = {
        "available_read_tools": READ_TOOL_NAMES,
        "available_propose_tools": PROPOSE_TOOL_NAMES,
        "owner_operations": owner_operation_catalog(),
        "rentals_summary": get_rentals_summary(session),
        "tenants_summary": get_tenants_summary(session),
        "properties_summary": get_properties_summary(session),
        "overdue_payments": get_overdue_payments(dashboard)[:30],
        "contracts_ending_soon": get_contracts_ending_soon(session, 30)[:30],
        "owner_chat_context": get_owner_chat_context(session, chat_id, 8),
        "properties": [
            {
                "object_id": item.id,
                "name": item.name,
                "active": item.active,
                "apartments": [
                    {
                        "apartment_id": apartment.id,
                        "name": apartment.name,
                        "active": apartment.active,
                        "odn_share_percent": apartment.odn_share_percent,
                    }
                    for apartment in item.apartments
                ],
            }
            for item in objects
        ],
        "utility_services": [
            {
                "service_id": item.id,
                "object_id": item.object_id,
                "object": item.object.name,
                "name": item.name,
                "kind": item.kind,
                "provider_reading_due_day": item.provider_reading_due_day,
                "provider_due_day": item.provider_due_day,
                "resident_due_days": item.resident_due_days,
                "meters": [
                    {
                        "meter_id": meter.id,
                        "name": meter.name,
                        "scope": meter.scope,
                        "apartment_id": meter.apartment_id,
                        "active": meter.active,
                    }
                    for meter in item.meters
                ],
            }
            for item in services
        ],
        "recent_utility_bills": [
            {
                "bill_id": item.id,
                "service_id": item.service_id,
                "object": item.service.object.name,
                "service": item.service.name,
                "period_start": item.period_start.isoformat(),
                "period_end": item.period_end.isoformat(),
                "status": item.status,
                "provider_paid": item.provider_paid,
            }
            for item in bills
        ],
        "open_utility_lines": [
            {
                "line_id": item.id,
                "bill_id": item.bill_id,
                "lease_id": item.lease_id,
                "apartment_id": item.apartment_id,
                "amount": item.total_amount,
                "paid": item.paid_amount,
                "status": item.status,
                "due_date": item.due_date.isoformat() if item.due_date else None,
            }
            for item in utility_lines
        ],
        "active_manual_debts": [
            {
                "debt_id": item.id,
                "lease_id": item.lease_id,
                "title": item.title,
                "amount": item.amount,
                "paid_amount": item.paid_amount,
                "due_date": item.due_date.isoformat() if item.due_date else None,
                "status": item.status,
            }
            for item in manual_debts
        ],
        "recent_payment_receipts": [
            {
                "receipt_id": item.id,
                "lease_id": item.lease_id,
                "rent_charge_id": item.rent_charge_id,
                "utility_line_id": item.utility_line_id,
                "amount": item.amount,
                "channel": item.channel,
                "status": item.status,
                "paid_at": item.paid_at.isoformat(),
            }
            for item in receipts
        ],
        "recent_expenses": [
            {
                "expense_id": item.id,
                "object_id": item.object_id,
                "apartment_id": item.apartment_id,
                "expense_date": item.expense_date.isoformat(),
                "category": item.category,
                "amount": item.amount,
                "source_funds": item.source_funds,
                "compensation_status": item.compensation_status,
            }
            for item in expenses
        ],
        "payment_situations": [
            {
                "situation_id": item.id,
                "lease_id": item.lease_id,
                "kind": item.kind,
                "reference_id": item.reference_id,
                "status": item.status,
                "mode": item.mode,
                "notification_count": item.notification_count,
                "tenant_response_count": item.tenant_response_count,
                "promise_date": item.promise_date.isoformat() if item.promise_date else None,
                "paused_until": item.paused_until.isoformat() if item.paused_until else None,
            }
            for item in payment_situations
        ],
        "safety": (
            "Read data may be returned immediately. Every owner_operation only creates a pending proposal. "
            "Execution is possible exclusively through the Telegram confirmation button."
        ),
    }
    return "Rental Manager AI internal read/propose tool snapshot:\n" + json.dumps(
        snapshot,
        ensure_ascii=False,
        default=str,
    )


def propose_send_message_to_tenant(lease_id: int, text: str) -> dict[str, Any]:
    return {"type": "send_tenant_message", "payload": {"lease_id": int(lease_id), "text": text}}


def propose_defer_rent(
    lease_id: int,
    charge_id: int,
    deferral_until: str,
    *,
    note: str = "",
    notify_tenant: bool = False,
    tenant_message: str = "",
) -> dict[str, Any]:
    return {
        "type": "defer_rent",
        "payload": {
            "lease_id": int(lease_id),
            "charge_id": int(charge_id),
            "deferral_until": deferral_until,
            "note": note,
            "notify_tenant": notify_tenant,
            "tenant_message": tenant_message,
        },
    }


def propose_move_out(
    lease_id: int,
    end_date: str,
    *,
    notes: str = "",
    notify_tenant: bool = False,
    tenant_message: str = "",
) -> dict[str, Any]:
    return {
        "type": "move_out",
        "payload": {
            "lease_id": int(lease_id),
            "end_date": end_date,
            "notes": notes,
            "notify_tenant": notify_tenant,
            "tenant_message": tenant_message,
        },
    }


def propose_create_manual_debt(
    lease_id: int,
    title: str,
    amount: float,
    due_date: str,
    *,
    note: str = "",
) -> dict[str, Any]:
    return {
        "type": "create_manual_debt",
        "payload": {
            "lease_id": int(lease_id),
            "title": title,
            "amount": amount,
            "due_date": due_date,
            "note": note,
        },
    }
