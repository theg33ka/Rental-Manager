from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
import json
import re
from typing import Any, TypedDict

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from rental_manager.models import (
    Apartment,
    CaseMemory,
    DomainEvent,
    Expense,
    Lease,
    ManualDebt,
    Meter,
    MeterReading,
    OperationalCase,
    OwnerCommitment,
    PaymentReceipt,
    PaymentSituation,
    ReminderOutcome,
    RentalObject,
    RentCharge,
    UtilityBill,
    UtilityBillLine,
    UtilityService,
    utc_now,
)
from rental_manager.services.hermes.events import emit_domain_event, stable_hash


OPEN_CASE_STATUSES = {"new", "active", "waiting_owner", "waiting_tenant", "auto_monitoring", "snoozed"}
FINAL_CASE_STATUSES = {"resolved", "cancelled"}
MANAGED_CASE_TYPES = {
    "rent_due",
    "rent_overdue",
    "rent_partial",
    "utility_overdue",
    "manual_debt",
    "broken_payment_promise",
    "deferral_request",
    "suspicious_payment",
    "stale_meter_reading",
    "expense_compensation",
    "tenant_message_escalation",
    "owner_commitment",
    "automation_failure",
}
CASE_ALIAS_STOPWORDS = {
    "как",
    "там",
    "что",
    "это",
    "кто",
    "кого",
    "где",
    "какие",
    "какой",
    "покажи",
    "найди",
    "нужно",
    "требует",
    "требуют",
    "внимания",
    "список",
    "сводка",
    "следующие",
    "шаги",
}
CASE_ALIAS_TOPIC_PREFIXES = (
    "аренд",
    "долг",
    "долж",
    "коммунал",
    "оплат",
    "платеж",
    "платёж",
    "просроч",
    "напомин",
    "показан",
    "счет",
    "счёт",
    "чек",
    "расход",
    "компенсац",
    "риск",
    "кейс",
    "case",
)
CASE_ALIAS_SUFFIXES = (
    "ого",
    "ему",
    "ому",
    "ыми",
    "ими",
    "ами",
    "ями",
    "ую",
    "юю",
    "ий",
    "ый",
    "ой",
    "ая",
    "яя",
    "ое",
    "ее",
    "ов",
    "ев",
    "ей",
    "ам",
    "ям",
    "ах",
    "ях",
    "ом",
    "ем",
    "ы",
    "и",
    "а",
    "я",
    "у",
    "ю",
    "е",
)


@dataclass(frozen=True)
class CaseCandidate:
    case_key: str
    case_type: str
    title: str
    compact_summary: str
    severity: str = "medium"
    amount_total: float = 0
    property_id: int | None = None
    apartment_id: int | None = None
    tenant_id: int | None = None
    contract_id: int | None = None
    waiting_for: str = ""
    assigned_actor: str = "hermes"
    next_review_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class LeaseRefs(TypedDict):
    property_id: int | None
    apartment_id: int | None
    tenant_id: int | None
    contract_id: int | None


def _money(value: float) -> str:
    rounded = round(float(value or 0), 2)
    rendered = f"{rounded:,.2f}".replace(",", " ").replace(".00", "")
    return f"{rendered} ₽"


def _at_local_noon(value: date) -> datetime:
    return datetime.combine(value, time(hour=12))


def _severity(days_overdue: int, amount: float, *, broken_promise: bool = False) -> str:
    if broken_promise or days_overdue >= 14 or amount >= 100_000:
        return "critical"
    if days_overdue >= 5 or amount >= 40_000:
        return "high"
    if days_overdue > 0 or amount >= 10_000:
        return "medium"
    return "low"


def _priority(candidate: CaseCandidate, first_seen_at: datetime | None = None) -> float:
    severity_score = {"critical": 100, "high": 70, "medium": 40, "low": 10}.get(candidate.severity, 30)
    amount_score = min(30, max(0, candidate.amount_total) / 2_000)
    due_score = 0
    due_text = candidate.metadata.get("due_date")
    if due_text:
        try:
            due_score = min(30, max(0, (date.today() - date.fromisoformat(str(due_text))).days) * 2)
        except ValueError:
            pass
    waiting_score = 15 if candidate.waiting_for == "owner" else 5 if candidate.waiting_for == "tenant" else 0
    age_score = 0
    if first_seen_at:
        age_score = min(15, max(0, (date.today() - first_seen_at.date()).days))
    return round(severity_score + amount_score + due_score + waiting_score + age_score, 2)


def _lease_refs(lease: Lease) -> LeaseRefs:
    return {
        "property_id": lease.apartment.object_id,
        "apartment_id": lease.apartment_id,
        "tenant_id": lease.tenant_id,
        "contract_id": lease.id,
    }


def _rent_candidates(session: Session, today: date) -> list[CaseCandidate]:
    charges = session.scalars(
        select(RentCharge)
        .options(
            joinedload(RentCharge.lease).joinedload(Lease.apartment).joinedload(Apartment.object),
            joinedload(RentCharge.lease).joinedload(Lease.tenant),
        )
        .join(Lease)
        .where(Lease.active.is_(True))
        .order_by(RentCharge.due_date, RentCharge.id)
    ).all()
    result: list[CaseCandidate] = []
    for charge in charges:
        due_total = float(charge.ip_due or 0) + float(charge.personal_due or 0)
        paid_total = float(charge.ip_paid or 0) + float(charge.personal_paid or 0)
        debt = round(max(0, due_total - paid_total), 2)
        if debt <= 0:
            continue
        effective_due = max(charge.due_date, charge.deferral_until) if charge.deferral_until else charge.due_date
        days_overdue = max(0, (today - effective_due).days)
        if paid_total > 0:
            case_type = "rent_partial"
        elif effective_due < today:
            case_type = "rent_overdue"
        elif effective_due <= today + timedelta(days=3):
            case_type = "rent_due"
        else:
            continue
        lease = charge.lease
        place = f"{lease.apartment.object.short_code or lease.apartment.object.name}-{lease.apartment.name}"
        summary = f"Аренда {_money(debt)}"
        if days_overdue:
            summary += f", просрочка {days_overdue} дн."
        elif charge.deferral_until:
            summary += f", пауза до {charge.deferral_until:%d.%m}"
        else:
            summary += f", срок {effective_due:%d.%m}"
        result.append(
            CaseCandidate(
                case_key=f"rent:{charge.id}",
                case_type=case_type,
                title=f"{place}: аренда",
                compact_summary=summary,
                severity=_severity(days_overdue, debt),
                amount_total=debt,
                next_review_at=_at_local_noon(effective_due if effective_due >= today else today + timedelta(days=1)),
                metadata={
                    "rent_charge_id": charge.id,
                    "due_date": effective_due.isoformat(),
                    "original_due_date": charge.due_date.isoformat(),
                    "paid_total": round(paid_total, 2),
                    "days_overdue": days_overdue,
                },
                **_lease_refs(lease),
            )
        )
    return result


def _utility_candidates(session: Session, today: date) -> list[CaseCandidate]:
    lines = session.scalars(
        select(UtilityBillLine)
        .options(
            joinedload(UtilityBillLine.lease).joinedload(Lease.apartment).joinedload(Apartment.object),
            joinedload(UtilityBillLine.lease).joinedload(Lease.tenant),
            joinedload(UtilityBillLine.bill).joinedload(UtilityBill.service),
        )
        .where(UtilityBillLine.lease_id.is_not(None))
        .order_by(UtilityBillLine.due_date, UtilityBillLine.id)
    ).all()
    result: list[CaseCandidate] = []
    for line in lines:
        if not line.lease or not line.due_date:
            continue
        debt = round(max(0, float(line.total_amount or 0) - float(line.paid_amount or 0)), 2)
        if debt <= 0 or line.status in {"draft", "cancelled"}:
            continue
        days_overdue = max(0, (today - line.due_date).days)
        if days_overdue <= 0:
            continue
        lease = line.lease
        service_name = line.bill.service.name if line.bill and line.bill.service else "коммуналка"
        place = f"{lease.apartment.object.short_code or lease.apartment.object.name}-{lease.apartment.name}"
        result.append(
            CaseCandidate(
                case_key=f"utility:{line.id}",
                case_type="utility_overdue",
                title=f"{place}: {service_name}",
                compact_summary=f"Коммуналка {_money(debt)}, просрочка {days_overdue} дн.",
                severity=_severity(days_overdue, debt),
                amount_total=debt,
                next_review_at=_at_local_noon(today + timedelta(days=1)),
                metadata={
                    "utility_line_id": line.id,
                    "utility_bill_id": line.bill_id,
                    "due_date": line.due_date.isoformat(),
                    "days_overdue": days_overdue,
                    "service": service_name,
                },
                **_lease_refs(lease),
            )
        )
    return result


def _manual_debt_candidates(session: Session, today: date) -> list[CaseCandidate]:
    debts = session.scalars(
        select(ManualDebt)
        .options(
            joinedload(ManualDebt.lease).joinedload(Lease.apartment).joinedload(Apartment.object),
            joinedload(ManualDebt.lease).joinedload(Lease.tenant),
        )
        .where(ManualDebt.active.is_(True))
        .order_by(ManualDebt.due_date, ManualDebt.id)
    ).all()
    result: list[CaseCandidate] = []
    for debt in debts:
        amount = round(max(0, float(debt.amount or 0) - float(debt.paid_amount or 0)), 2)
        if amount <= 0 or debt.status in {"paid", "cancelled"}:
            continue
        overdue = max(0, (today - debt.due_date).days) if debt.due_date else 0
        lease = debt.lease
        place = f"{lease.apartment.object.short_code or lease.apartment.object.name}-{lease.apartment.name}"
        result.append(
            CaseCandidate(
                case_key=f"manual-debt:{debt.id}",
                case_type="manual_debt",
                title=f"{place}: {debt.title or 'долг'}",
                compact_summary=f"{debt.title or 'Долг'} {_money(amount)}" + (f", просрочка {overdue} дн." if overdue else ""),
                severity=_severity(overdue, amount),
                amount_total=amount,
                next_review_at=_at_local_noon(today + timedelta(days=1)),
                metadata={"manual_debt_id": debt.id, "due_date": debt.due_date.isoformat() if debt.due_date else None},
                **_lease_refs(lease),
            )
        )
    return result


def _payment_situation_candidates(session: Session, today: date) -> list[CaseCandidate]:
    situations = session.scalars(
        select(PaymentSituation)
        .options(joinedload(PaymentSituation.lease).joinedload(Lease.apartment).joinedload(Apartment.object), joinedload(PaymentSituation.lease).joinedload(Lease.tenant))
        .where(PaymentSituation.status.not_in({"paid", "closed", "resolved", "cancelled"}))
    ).all()
    result: list[CaseCandidate] = []
    for situation in situations:
        lease = situation.lease
        if not lease:
            continue
        refs = _lease_refs(lease)
        place = f"{lease.apartment.object.short_code or lease.apartment.object.name}-{lease.apartment.name}"
        if situation.promise_date and situation.promise_date < today and situation.status not in {"awaiting_receipt"}:
            days = (today - situation.promise_date).days
            result.append(
                CaseCandidate(
                    case_key=f"broken-promise:{situation.id}",
                    case_type="broken_payment_promise",
                    title=f"{place}: нарушено обещание",
                    compact_summary=f"Обещанная дата прошла {days} дн. назад.",
                    severity=_severity(days, 0, broken_promise=True),
                    waiting_for="tenant",
                    next_review_at=_at_local_noon(today + timedelta(days=1)),
                    metadata={"payment_situation_id": situation.id, "promise_date": situation.promise_date.isoformat()},
                    **refs,
                )
            )
        if situation.status in {"question", "escalated"}:
            has_deferral = bool(situation.promise_date and situation.promise_date >= today)
            result.append(
                CaseCandidate(
                    case_key=f"tenant-escalation:{situation.id}",
                    case_type="deferral_request" if has_deferral else "tenant_message_escalation",
                    title=f"{place}: нужно решение владельца",
                    compact_summary=(
                        f"Запрошен перенос до {situation.promise_date:%d.%m}." if has_deferral else "Жилец ждёт решения владельца."
                    ),
                    severity="high" if has_deferral else "medium",
                    waiting_for="owner",
                    assigned_actor="owner",
                    next_review_at=_at_local_noon(today),
                    metadata={"payment_situation_id": situation.id, "promise_date": situation.promise_date.isoformat() if situation.promise_date else None},
                    **refs,
                )
            )
    return result


def _suspicious_payment_candidates(session: Session) -> list[CaseCandidate]:
    receipts = session.scalars(
        select(PaymentReceipt).where(PaymentReceipt.status.in_({"suspicious", "pending_review"}))
    ).all()
    result: list[CaseCandidate] = []
    for receipt in receipts:
        lease = session.get(Lease, receipt.lease_id) if receipt.lease_id else None
        refs: LeaseRefs = _lease_refs(lease) if lease else {
            "property_id": None,
            "apartment_id": receipt.apartment_id,
            "tenant_id": None,
            "contract_id": receipt.lease_id,
        }
        result.append(
            CaseCandidate(
                case_key=f"suspicious-payment:{receipt.id}",
                case_type="suspicious_payment",
                title=f"Проверить платёж #{receipt.id}",
                compact_summary=f"Платёж {_money(receipt.amount)} требует проверки.",
                severity="high",
                amount_total=float(receipt.amount or 0),
                waiting_for="owner",
                assigned_actor="owner",
                metadata={"payment_receipt_id": receipt.id},
                **refs,
            )
        )
    return result


def _stale_reading_candidates(session: Session, today: date, stale_days: int = 14) -> list[CaseCandidate]:
    rows = session.execute(
        select(
            UtilityService.id,
            UtilityService.object_id,
            UtilityService.name,
            RentalObject.name,
            RentalObject.short_code,
            func.max(MeterReading.reading_date),
        )
        .join(RentalObject, UtilityService.object_id == RentalObject.id)
        .join(Meter, Meter.service_id == UtilityService.id)
        .outerjoin(MeterReading, MeterReading.meter_id == Meter.id)
        .where(UtilityService.active.is_(True), Meter.active.is_(True))
        .group_by(UtilityService.id, UtilityService.object_id, UtilityService.name, RentalObject.name, RentalObject.short_code)
    ).all()
    result: list[CaseCandidate] = []
    for service_id, object_id, service_name, object_name, short_code, last_date in rows:
        age = (today - last_date).days if last_date else stale_days + 1
        if age <= stale_days:
            continue
        result.append(
            CaseCandidate(
                case_key=f"stale-reading:{service_id}",
                case_type="stale_meter_reading",
                title=f"{short_code or object_name}: показания {service_name}",
                compact_summary=f"Нет свежих показаний {age} дн." if last_date else "Показаний ещё нет.",
                severity="high" if age >= 30 else "medium",
                property_id=object_id,
                next_review_at=_at_local_noon(today + timedelta(days=1)),
                metadata={"service_id": service_id, "last_reading_date": last_date.isoformat() if last_date else None, "age_days": age},
            )
        )
    return result


def _expense_candidates(session: Session) -> list[CaseCandidate]:
    expenses = session.scalars(
        select(Expense).where(
            Expense.source_funds == "personal",
            Expense.compensation_status.not_in({"compensated", "not_required", "cancelled"}),
        )
    ).all()
    return [
        CaseCandidate(
            case_key=f"expense:{expense.id}",
            case_type="expense_compensation",
            title=f"Компенсировать расход: {expense.category or 'расход'}",
            compact_summary=f"К компенсации {_money(expense.amount)}.",
            severity="medium",
            amount_total=float(expense.amount or 0),
            property_id=expense.object_id,
            apartment_id=expense.apartment_id,
            waiting_for="owner",
            assigned_actor="owner",
            metadata={"expense_id": expense.id, "expense_date": expense.expense_date.isoformat()},
        )
        for expense in expenses
    ]


def _commitment_candidates(session: Session, today: date) -> list[CaseCandidate]:
    commitments = session.scalars(
        select(OwnerCommitment).where(OwnerCommitment.status.in_({"active", "overdue"}))
    ).all()
    result: list[CaseCandidate] = []
    for commitment in commitments:
        if commitment.case_id:
            continue
        overdue = bool(commitment.due_at and commitment.due_at.date() < today)
        result.append(
            CaseCandidate(
                case_key=f"owner-commitment:{commitment.id}",
                case_type="owner_commitment",
                title="Обязательство владельца",
                compact_summary=commitment.description[:300],
                severity="high" if overdue else "medium",
                waiting_for="owner",
                assigned_actor="owner",
                next_review_at=commitment.due_at,
                metadata={"owner_commitment_id": commitment.id, "due_at": commitment.due_at.isoformat() if commitment.due_at else None},
            )
        )
    return result


def _automation_failure_candidates(session: Session) -> list[CaseCandidate]:
    events = session.scalars(
        select(DomainEvent)
        .where(DomainEvent.event_type == "automation_failed", DomainEvent.processed_at.is_(None))
        .order_by(DomainEvent.occurred_at, DomainEvent.id)
    ).all()
    return [
        CaseCandidate(
            case_key=f"automation-failure:{event.entity_type}:{event.entity_id or event.id}",
            case_type="automation_failure",
            title="Ошибка автоматизации",
            compact_summary=_event_summary(event),
            severity="high",
            property_id=event.property_id,
            apartment_id=event.apartment_id,
            tenant_id=event.tenant_id,
            contract_id=event.contract_id,
            waiting_for="owner",
            assigned_actor="owner",
            metadata={"domain_event_id": event.id},
        )
        for event in events
    ]


def _event_summary(event: DomainEvent) -> str:
    try:
        payload = json.loads(event.payload_json or "{}")
    except json.JSONDecodeError:
        payload = {}
    return str(payload.get("error") or payload.get("message") or "Автоматическое действие завершилось ошибкой.")[:400]


def collect_case_candidates(session: Session, today: date | None = None) -> list[CaseCandidate]:
    today = today or date.today()
    return [
        *_rent_candidates(session, today),
        *_utility_candidates(session, today),
        *_manual_debt_candidates(session, today),
        *_payment_situation_candidates(session, today),
        *_suspicious_payment_candidates(session),
        *_stale_reading_candidates(session, today),
        *_expense_candidates(session),
        *_commitment_candidates(session, today),
        *_automation_failure_candidates(session),
    ]


def _candidate_state(candidate: CaseCandidate) -> dict[str, Any]:
    return {
        "case_type": candidate.case_type,
        "severity": candidate.severity,
        "title": candidate.title,
        "summary": candidate.compact_summary,
        "amount": round(candidate.amount_total, 2),
        "property_id": candidate.property_id,
        "apartment_id": candidate.apartment_id,
        "tenant_id": candidate.tenant_id,
        "contract_id": candidate.contract_id,
        "waiting_for": candidate.waiting_for,
        "metadata": candidate.metadata,
    }


def upsert_operational_case(session: Session, candidate: CaseCandidate) -> OperationalCase:
    item = session.scalar(select(OperationalCase).where(OperationalCase.case_key == candidate.case_key).limit(1))
    now = utc_now()
    state_hash = stable_hash(_candidate_state(candidate))
    created = item is None
    reopened = False
    if not item:
        item = OperationalCase(
            case_key=candidate.case_key,
            case_type=candidate.case_type,
            status="new",
            first_seen_at=now,
            last_changed_at=now,
        )
        session.add(item)
    changed = item.state_hash != state_hash
    if item.status in FINAL_CASE_STATUSES:
        item.status = "active"
        item.resolved_at = None
        item.resolution_reason = ""
        changed = True
        reopened = True
    if item.status == "snoozed" and item.suppression_until and item.suppression_until <= now:
        item.status = "active"
        item.suppression_until = None
    item.case_type = candidate.case_type
    item.severity = candidate.severity
    item.property_id = candidate.property_id
    item.apartment_id = candidate.apartment_id
    item.tenant_id = candidate.tenant_id
    item.contract_id = candidate.contract_id
    item.title = candidate.title[:240]
    item.compact_summary = candidate.compact_summary[:1200]
    item.amount_total = round(candidate.amount_total, 2)
    item.currency = "RUB"
    item.next_review_at = candidate.next_review_at
    item.assigned_actor = candidate.assigned_actor
    item.waiting_for = candidate.waiting_for
    item.metadata_json = json.dumps(candidate.metadata, ensure_ascii=False, sort_keys=True)
    item.priority_score = _priority(candidate, item.first_seen_at)
    if changed:
        item.last_changed_at = now
        item.state_hash = state_hash
        if item.status != "snoozed":
            item.status = (
                "waiting_owner"
                if candidate.waiting_for == "owner"
                else "waiting_tenant"
                if candidate.waiting_for == "tenant"
                else "auto_monitoring"
            )
    session.flush()
    if created or changed:
        emit_domain_event(
            session,
            "operational_case_created" if created else "operational_case_reopened" if reopened else "operational_case_changed",
            entity_type="OperationalCase",
            entity_id=item.id,
            property_id=item.property_id,
            apartment_id=item.apartment_id,
            tenant_id=item.tenant_id,
            contract_id=item.contract_id,
            payload={"case_type": item.case_type, "status": item.status, "state_hash": item.state_hash},
            idempotency_key=f"case:{item.id}:{item.state_hash}:{'created' if created else 'reopened' if reopened else 'changed'}",
        )
    if candidate.case_type == "broken_payment_promise" and (created or reopened):
        if item.contract_id:
            from rental_manager.services.hermes.reminders import record_broken_promise

            record_broken_promise(session, lease_id=item.contract_id)
        emit_domain_event(
            session,
            "payment_promise_expired",
            entity_type="PaymentSituation",
            entity_id=candidate.metadata.get("payment_situation_id"),
            property_id=item.property_id,
            apartment_id=item.apartment_id,
            tenant_id=item.tenant_id,
            contract_id=item.contract_id,
            payload={"case_id": item.id, "promise_date": candidate.metadata.get("promise_date")},
        )
    if candidate.case_type == "stale_meter_reading" and created:
        emit_domain_event(
            session,
            "meter_reading_stale",
            entity_type="UtilityService",
            entity_id=candidate.metadata.get("service_id"),
            property_id=item.property_id,
            payload={"case_id": item.id, "age_days": candidate.metadata.get("age_days")},
        )
    sync_case_memory(session, item)
    return item


def reconcile_operational_cases(session: Session, today: date | None = None) -> list[OperationalCase]:
    candidates = collect_case_candidates(session, today)
    seen: set[str] = set()
    for candidate in candidates:
        seen.add(candidate.case_key)
        upsert_operational_case(session, candidate)
    open_cases = session.scalars(
        select(OperationalCase).where(
            OperationalCase.case_type.in_(MANAGED_CASE_TYPES),
            OperationalCase.status.in_(OPEN_CASE_STATUSES),
        )
    ).all()
    now = utc_now()
    for item in open_cases:
        if item.case_key in seen:
            continue
        item.status = "resolved"
        item.resolved_at = now
        item.resolution_reason = "Источник проблемы больше не подтверждается"
        item.last_changed_at = now
        item.state_hash = stable_hash({"status": "resolved", "reason": item.resolution_reason})
        if item.contract_id and item.case_type in {"rent_due", "rent_overdue", "rent_partial", "utility_overdue", "manual_debt"}:
            outcome = session.scalar(
                select(ReminderOutcome)
                .where(
                    ReminderOutcome.contract_id == item.contract_id,
                    ReminderOutcome.outcome.in_({"delivered", "replied", "promised", "ignored"}),
                )
                .order_by(ReminderOutcome.sent_at.desc(), ReminderOutcome.id.desc())
                .limit(1)
            )
            if outcome:
                outcome.outcome = "paid"
                outcome.outcome_at = now
        emit_domain_event(
            session,
            "operational_case_resolved",
            entity_type="OperationalCase",
            entity_id=item.id,
            property_id=item.property_id,
            apartment_id=item.apartment_id,
            tenant_id=item.tenant_id,
            contract_id=item.contract_id,
            payload={"case_type": item.case_type, "reason": item.resolution_reason},
            idempotency_key=f"case:{item.id}:resolved:{item.state_hash}",
        )
        sync_case_memory(session, item)
    session.flush()
    for event in session.scalars(select(DomainEvent).where(DomainEvent.processed_at.is_(None))).all():
        event.processed_at = now
    return list(
        session.scalars(
            select(OperationalCase)
            .where(OperationalCase.status.in_(OPEN_CASE_STATUSES))
            .order_by(OperationalCase.priority_score.desc(), OperationalCase.last_changed_at.desc(), OperationalCase.id)
        ).all()
    )


def sync_case_memory(session: Session, item: OperationalCase, *, important_message: str = "") -> CaseMemory:
    memory = session.scalar(select(CaseMemory).where(CaseMemory.case_id == item.id).limit(1))
    if not memory:
        memory = CaseMemory(case_id=item.id)
        session.add(memory)
    if memory.state_hash != item.state_hash or important_message:
        summary = item.compact_summary.strip()
        if item.waiting_for:
            summary += f" Ожидается действие: {item.waiting_for}."
        if important_message:
            summary += f" Последнее важное сообщение: {important_message.strip()[:400]}"
        memory.rolling_summary = summary[:1200]
        memory.state_hash = item.state_hash
        memory.updated_at = utc_now()
    return memory


def case_label(session: Session, item: OperationalCase) -> str:
    if item.apartment_id:
        apartment = session.get(Apartment, item.apartment_id)
        if apartment:
            rental_object = session.get(RentalObject, apartment.object_id)
            prefix = (rental_object.short_code or rental_object.name) if rental_object else "Объект"
            return f"{prefix}-{apartment.name}"
    if item.property_id:
        rental_object = session.get(RentalObject, item.property_id)
        if rental_object:
            return rental_object.short_code or rental_object.name
    return f"Кейс {item.id}"


def case_snapshot(session: Session, item: OperationalCase) -> dict[str, Any]:
    try:
        metadata = json.loads(item.metadata_json or "{}")
    except json.JSONDecodeError:
        metadata = {}
    memory = session.scalar(select(CaseMemory).where(CaseMemory.case_id == item.id).limit(1))
    return {
        "id": item.id,
        "label": case_label(session, item),
        "case_type": item.case_type,
        "status": item.status,
        "severity": item.severity,
        "priority_score": item.priority_score,
        "property_id": item.property_id,
        "apartment_id": item.apartment_id,
        "tenant_id": item.tenant_id,
        "contract_id": item.contract_id,
        "title": item.title,
        "compact_summary": item.compact_summary,
        "rolling_summary": memory.rolling_summary if memory else item.compact_summary,
        "amount_total": item.amount_total,
        "currency": item.currency,
        "first_seen_at": item.first_seen_at.isoformat(),
        "last_changed_at": item.last_changed_at.isoformat(),
        "next_review_at": item.next_review_at.isoformat() if item.next_review_at else None,
        "assigned_actor": item.assigned_actor,
        "waiting_for": item.waiting_for,
        "suppression_until": item.suppression_until.isoformat() if item.suppression_until else None,
        "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None,
        "resolution_reason": item.resolution_reason,
        "state_hash": item.state_hash,
        "metadata": metadata,
    }


def _case_alias_stem(token: str) -> str:
    for suffix in CASE_ALIAS_SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: -len(suffix)]
    return token


def resolve_case_alias(session: Session, text: str, *, open_only: bool = True) -> list[OperationalCase]:
    normalized = re.sub(r"[^0-9a-zа-яё]+", " ", (text or "").lower()).strip()
    id_match = re.search(r"(?:кейс|case)\s*(\d+)", normalized)
    if id_match:
        item = session.get(OperationalCase, int(id_match.group(1)))
        if item and (not open_only or item.status in OPEN_CASE_STATUSES):
            return [item]
        return []
    query = select(OperationalCase)
    if open_only:
        query = query.where(OperationalCase.status.in_(OPEN_CASE_STATUSES))
    cases = session.scalars(query.order_by(OperationalCase.priority_score.desc(), OperationalCase.id)).all()
    scored: list[tuple[int, OperationalCase]] = []
    tokens = {
        token
        for token in normalized.split()
        if len(token) >= 3 and token not in CASE_ALIAS_STOPWORDS and not token.startswith(CASE_ALIAS_TOPIC_PREFIXES)
    }
    if not tokens:
        return []
    for item in cases:
        label = re.sub(r"[^0-9a-zа-яё]+", " ", case_label(session, item).lower())
        lease = session.get(Lease, item.contract_id) if item.contract_id else None
        identity = " ".join(
            [
                label,
                item.title.lower(),
                lease.tenant.full_name.lower() if lease and lease.tenant else "",
            ]
        )
        identity_stems = {
            _case_alias_stem(word)
            for word in re.sub(r"[^0-9a-zа-яё]+", " ", identity).split()
            if len(word) >= 3
        }
        score = sum(1 for token in tokens if _case_alias_stem(token) in identity_stems)
        if score:
            scored.append((score, item))
    if not scored:
        return []
    best = max(score for score, _item in scored)
    return [item for score, item in scored if score == best][:8]


def close_case(session: Session, case_id: int, reason: str = "Закрыто владельцем") -> OperationalCase:
    item = session.get(OperationalCase, case_id)
    if not item:
        raise ValueError("Кейс не найден")
    item.status = "resolved"
    item.resolved_at = utc_now()
    item.resolution_reason = reason[:1000]
    item.last_changed_at = utc_now()
    item.state_hash = stable_hash({"status": item.status, "reason": item.resolution_reason})
    sync_case_memory(session, item)
    return item


def snooze_case(session: Session, case_id: int, until: datetime) -> OperationalCase:
    item = session.get(OperationalCase, case_id)
    if not item:
        raise ValueError("Кейс не найден")
    if until <= utc_now():
        raise ValueError("Срок паузы должен быть в будущем")
    item.status = "snoozed"
    item.suppression_until = until
    item.next_review_at = until
    item.last_changed_at = utc_now()
    return item
