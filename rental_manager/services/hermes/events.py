from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
from typing import Any
import uuid

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session

from rental_manager.models import (
    AgentActionProposal,
    AiMessage,
    DomainEvent,
    Expense,
    Lease,
    ManualDebt,
    MessageLog,
    MeterReading,
    OwnerCommitment,
    PaymentReceipt,
    PaymentSituation,
    RentCharge,
    UtilityBill,
    UtilityBillLine,
    utc_now,
)


_LISTENERS_INSTALLED = False


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=_json_default).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def emit_domain_event(
    session: Session,
    event_type: str,
    *,
    entity_type: str = "",
    entity_id: int | str | None = None,
    property_id: int | None = None,
    apartment_id: int | None = None,
    tenant_id: int | None = None,
    contract_id: int | None = None,
    actor_type: str | None = None,
    source: str | None = None,
    payload: dict[str, Any] | None = None,
    correlation_id: str | None = None,
    idempotency_key: str | None = None,
    occurred_at: datetime | None = None,
) -> DomainEvent:
    payload = payload or {}
    correlation_id = correlation_id or str(session.info.get("correlation_id") or uuid.uuid4())
    entity_ref = "" if entity_id is None else str(entity_id)
    idempotency_key = idempotency_key or (
        f"{event_type}:{entity_type}:{entity_ref}:{stable_hash(payload)}"
    )
    if len(idempotency_key) > 200:
        idempotency_key = f"{idempotency_key[:120]}:{stable_hash(idempotency_key)}"
    pending = next(
        (
            item
            for item in session.new
            if isinstance(item, DomainEvent) and item.idempotency_key == idempotency_key
        ),
        None,
    )
    if pending:
        return pending
    with session.no_autoflush:
        existing = session.scalar(
            select(DomainEvent).where(DomainEvent.idempotency_key == idempotency_key).limit(1)
        )
    if existing:
        return existing
    item = DomainEvent(
        event_type=event_type,
        idempotency_key=idempotency_key,
        occurred_at=occurred_at or utc_now(),
        entity_type=entity_type[:80],
        entity_id=entity_ref[:80],
        property_id=property_id,
        apartment_id=apartment_id,
        tenant_id=tenant_id,
        contract_id=contract_id,
        actor_type=(actor_type or str(session.info.get("actor_type") or "system"))[:40],
        source=(source or str(session.info.get("event_source") or "backend"))[:80],
        payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True, default=_json_default),
        correlation_id=correlation_id[:120],
    )
    session.add(item)
    return item


def _changed(instance: Any, field: str) -> bool:
    state = inspect(instance)
    attribute = state.attrs.get(field)
    return bool(attribute and attribute.history.has_changes())


def _column_snapshot(instance: Any) -> dict[str, Any]:
    state = inspect(instance)
    result: dict[str, Any] = {}
    for attribute in state.mapper.column_attrs:
        key = attribute.key
        if key in {"created_at", "updated_at"}:
            continue
        result[key] = getattr(instance, key, None)
    return result


def _event_types(instance: Any, *, created: bool) -> list[str]:
    if isinstance(instance, RentCharge):
        result = ["charge_created" if created else "charge_changed"]
        status = str(instance.status or "")
        if status == "overdue":
            result.append("rent_overdue")
        elif status == "partial":
            result.append("rent_partially_paid")
        elif status == "paid":
            result.append("rent_fully_paid")
        if not created and _changed(instance, "deferral_until") and instance.deferral_until:
            result.append("deferral_granted")
        return result
    if isinstance(instance, PaymentReceipt):
        result = ["payment_recorded" if created else "payment_changed"]
        if str(instance.status or "") in {"suspicious", "pending_review"}:
            result.append("suspicious_payment")
        return result
    if isinstance(instance, PaymentSituation):
        result = ["payment_situation_created" if created else "payment_situation_changed"]
        if instance.promise_date and (created or _changed(instance, "promise_date")):
            result.append("payment_promised")
        if str(instance.status or "") in {"question", "escalated"}:
            result.append("deferral_requested" if instance.promise_date else "tenant_message_escalated")
        return result
    if isinstance(instance, UtilityBill):
        return ["utility_bill_created" if created else "utility_bill_changed"]
    if isinstance(instance, UtilityBillLine):
        result = ["utility_charge_created" if created else "utility_charge_changed"]
        if str(instance.status or "") == "overdue":
            result.append("utility_overdue")
        elif str(instance.status or "") in {"paid", "accepted"}:
            result.append("utility_fully_paid")
        return result
    if isinstance(instance, MeterReading):
        return ["meter_reading_recorded" if created else "meter_reading_changed"]
    if isinstance(instance, Expense):
        result = ["expense_created" if created else "expense_changed"]
        if str(instance.source_funds or "") == "personal" and str(instance.compensation_status or "") not in {
            "compensated",
            "not_required",
            "cancelled",
        }:
            result.append("expense_compensation_required")
        return result
    if isinstance(instance, ManualDebt):
        return ["manual_debt_created" if created else "manual_debt_changed"]
    if isinstance(instance, Lease):
        result = ["contract_created" if created else "contract_changed"]
        if not created and (_changed(instance, "end_date") or _changed(instance, "active")) and not instance.active:
            result.append("tenant_moved_out")
        return result
    if isinstance(instance, MessageLog):
        if str(instance.status or "") == "sent" and str(instance.template_key or ""):
            return ["reminder_sent"]
        return []
    if isinstance(instance, AiMessage):
        if instance.role == "user" and instance.lease_id:
            return ["tenant_message_received"]
        return []
    if isinstance(instance, AgentActionProposal):
        if str(instance.status or "") == "executed":
            return ["ai_proposal_executed"]
        return []
    if isinstance(instance, OwnerCommitment):
        if created:
            return ["owner_commitment_created"]
        if str(instance.status or "") == "completed":
            return ["owner_commitment_completed"]
        if str(instance.status or "") == "overdue":
            return ["owner_commitment_overdue"]
    return []


def _references(instance: Any) -> dict[str, int | None]:
    contract_id = getattr(instance, "lease_id", None)
    lease = getattr(instance, "lease", None)
    if isinstance(instance, Lease):
        contract_id = instance.id
        lease = instance
    apartment = getattr(lease, "apartment", None) if lease else None
    return {
        "property_id": getattr(instance, "object_id", None) or getattr(apartment, "object_id", None),
        "apartment_id": getattr(instance, "apartment_id", None) or getattr(lease, "apartment_id", None),
        "tenant_id": getattr(instance, "tenant_id", None) or getattr(lease, "tenant_id", None),
        "contract_id": contract_id,
    }


def _before_flush(session: Session, _flush_context: Any, _instances: Any) -> None:
    if session.info.get("_hermes_events_emitting"):
        return
    pending: list[dict[str, Any]] = session.info.setdefault("_hermes_pending_events", [])
    correlation_id = str(session.info.get("correlation_id") or uuid.uuid4())
    session.info["correlation_id"] = correlation_id
    for instance in list(session.new) + list(session.dirty):
        if isinstance(instance, DomainEvent):
            continue
        created = instance in session.new
        if not created and not session.is_modified(instance, include_collections=False):
            continue
        event_types = _event_types(instance, created=created)
        if not event_types:
            continue
        pending.append(
            {
                "instance": instance,
                "event_types": event_types,
                "correlation_id": correlation_id,
            }
        )


def _after_flush_postexec(session: Session, _flush_context: Any) -> None:
    pending = session.info.pop("_hermes_pending_events", [])
    if not pending:
        return
    session.info["_hermes_events_emitting"] = True
    try:
        for item in pending:
            instance = item["instance"]
            entity_id = getattr(instance, "id", None)
            entity_type = type(instance).__name__
            snapshot = _column_snapshot(instance)
            references = _references(instance)
            fingerprint = stable_hash(snapshot)
            for event_type in item["event_types"]:
                emit_domain_event(
                    session,
                    event_type,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    payload=snapshot,
                    correlation_id=item["correlation_id"],
                    idempotency_key=f"orm:{event_type}:{entity_type}:{entity_id}:{fingerprint}",
                    property_id=references["property_id"],
                    apartment_id=references["apartment_id"],
                    tenant_id=references["tenant_id"],
                    contract_id=references["contract_id"],
                )
    finally:
        session.info["_hermes_events_emitting"] = False


def install_domain_event_listeners() -> None:
    global _LISTENERS_INSTALLED
    if _LISTENERS_INSTALLED:
        return
    event.listen(Session, "before_flush", _before_flush)
    event.listen(Session, "after_flush_postexec", _after_flush_postexec)
    _LISTENERS_INSTALLED = True
