from __future__ import annotations

from datetime import date, datetime, time
import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from rental_manager.models import (
    Lease,
    OperationalCase,
    PaymentSituation,
    ReminderOutcome,
    TenantStrategyProfile,
    utc_now,
)
from rental_manager.services.hermes.events import emit_domain_event


TENANT_INTENTS = {
    "rent_question",
    "utility_question",
    "receipt_submission",
    "payment_promise",
    "deferral_request",
    "amount_dispute",
    "maintenance_issue",
    "emergency",
    "conflict",
    "owner_request",
    "general_question",
    "unsupported",
}


def classify_tenant_intent(text: str, *, has_attachment: bool = False) -> str:
    lowered = re.sub(r"\s+", " ", (text or "").strip().lower())
    if has_attachment or re.search(r"\b(чек|квитанц|перев[её]л|оплатил)\b", lowered):
        return "receipt_submission"
    if re.search(r"\b(пожар|дым|газ|затоп|прорвало|авари|искрит|нет электричества)\b", lowered):
        return "emergency"
    if re.search(r"\b(угрож|конфликт|скандал|суд|полици|жалоб)\b", lowered):
        return "conflict"
    if re.search(r"\b(сломал|сломано|не работает|ремонт|мастер|теч[её]т)\b", lowered):
        return "maintenance_issue"
    if re.search(r"\b(отсроч|перенести оплат|позже оплат|не успеваю)\b", lowered):
        return "deferral_request"
    if re.search(r"\b(оплачу|переведу|внесу).{0,50}(сегодня|завтра|через|\d{1,2}[./]\d{1,2})", lowered):
        return "payment_promise"
    if re.search(r"\b(не согласен|неверн|ошибк).{0,40}(сумм|начисл|долг)|откуда.{0,30}сумм", lowered):
        return "amount_dispute"
    if re.search(r"\b(коммунал|свет|вода|электр|газ|показани|сч[её]тчик)\b", lowered):
        return "utility_question"
    if re.search(r"\b(аренд|квартплат|оплат|плат[её]ж|долг|реквизит)\b", lowered):
        return "rent_question"
    if re.search(r"\b(владелец|хозяин|собственник|передайте)\b", lowered):
        return "owner_request"
    if "?" in lowered or re.search(r"\b(как|когда|где|почему|можно ли|подскажите)\b", lowered):
        return "general_question"
    return "unsupported"


def tenant_strategy(session: Session, lease: Lease) -> TenantStrategyProfile:
    profile = session.scalar(
        select(TenantStrategyProfile).where(TenantStrategyProfile.contract_id == lease.id).limit(1)
    )
    if profile:
        return profile
    profile = TenantStrategyProfile(tenant_id=lease.tenant_id, contract_id=lease.id)
    session.add(profile)
    session.flush()
    return profile


def tenant_mode(settings: dict[str, Any]) -> str:
    mode = str(settings.get("ai_tenant_mode") or "auto").strip().lower()
    return mode if mode in {"auto", "draft_only", "finance_only"} else "auto"


def tenant_response_policy(
    intent: str,
    settings: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now()
    mode = tenant_mode(settings)
    night = not (8 <= now.hour < 21)
    escalation = intent in {"maintenance_issue", "conflict", "owner_request", "amount_dispute", "deferral_request"}
    if intent == "emergency":
        return {"mode": "acknowledge", "escalate": True, "delay_escalation": False, "use_llm": False}
    if night:
        return {
            "mode": "acknowledge",
            "escalate": escalation,
            "delay_escalation": escalation,
            "use_llm": False,
        }
    if mode == "draft_only":
        return {"mode": "draft", "escalate": True, "delay_escalation": False, "use_llm": True}
    if mode == "finance_only" and intent not in {
        "rent_question",
        "utility_question",
        "receipt_submission",
        "payment_promise",
    }:
        return {"mode": "acknowledge", "escalate": True, "delay_escalation": False, "use_llm": False}
    if intent in {"maintenance_issue", "conflict", "deferral_request", "amount_dispute", "owner_request"}:
        return {"mode": "acknowledge", "escalate": True, "delay_escalation": False, "use_llm": False}
    return {"mode": "reply", "escalate": False, "delay_escalation": False, "use_llm": True}


def tenant_acknowledgement(intent: str, *, night: bool = False) -> str:
    if intent == "emergency":
        return "Сообщение принято и срочно передано владельцу. Если есть угроза жизни, звоните 112."
    if night:
        return "Сообщение принято. Владелец увидит его утром. Срочные аварийные сообщения передаются сразу."
    if intent == "deferral_request":
        return "Запрос принят и передан владельцу. До его решения срок оплаты не меняется."
    if intent in {"maintenance_issue", "conflict", "amount_dispute", "owner_request"}:
        return "Сообщение принято и передано владельцу. Ответ придёт после его решения."
    return "Сообщение принято. Уточню данные и отвечу по вашему договору."


def _case_for_contract(session: Session, contract_id: int) -> OperationalCase | None:
    return session.scalar(
        select(OperationalCase)
        .where(
            OperationalCase.contract_id == contract_id,
            OperationalCase.status.in_({"new", "active", "waiting_owner", "waiting_tenant", "auto_monitoring"}),
        )
        .order_by(OperationalCase.priority_score.desc(), OperationalCase.id)
        .limit(1)
    )


def record_reminder_outcome(
    session: Session,
    *,
    lease_id: int,
    stage: str,
    template_key: str,
    payment_situation_id: int | None = None,
    message_log_id: int | None = None,
    outcome: str = "delivered",
    metadata: dict[str, Any] | None = None,
    sent_at: datetime | None = None,
) -> ReminderOutcome:
    case = _case_for_contract(session, lease_id)
    actual_sent_at = sent_at or utc_now()
    previous = session.scalar(
        select(ReminderOutcome)
        .where(
            ReminderOutcome.contract_id == lease_id,
            ReminderOutcome.outcome == "delivered",
            ReminderOutcome.sent_at < datetime.combine(actual_sent_at.date(), time.min),
        )
        .order_by(ReminderOutcome.sent_at.desc(), ReminderOutcome.id.desc())
        .limit(1)
    )
    if previous:
        previous.outcome = "ignored"
        previous.outcome_at = actual_sent_at
        emit_domain_event(
            session,
            "reminder_no_response",
            entity_type="ReminderOutcome",
            entity_id=previous.id,
            contract_id=lease_id,
            payload={"stage": previous.stage, "sent_at": previous.sent_at.isoformat()},
            idempotency_key=f"reminder-no-response:{previous.id}",
        )
    item = ReminderOutcome(
        contract_id=lease_id,
        case_id=case.id if case else None,
        payment_situation_id=payment_situation_id,
        message_log_id=message_log_id,
        stage=stage[:40],
        template_key=template_key[:80],
        outcome=outcome,
        sent_at=actual_sent_at,
        outcome_at=utc_now(),
        metadata_json=json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
    )
    session.add(item)
    emit_domain_event(
        session,
        "reminder_sent",
        entity_type="ReminderOutcome",
        contract_id=lease_id,
        payload={
            "stage": stage[:40],
            "template_key": template_key[:80],
            "payment_situation_id": payment_situation_id,
            "sent_at": actual_sent_at.isoformat(),
        },
        idempotency_key=(
            f"reminder-sent:{lease_id}:{payment_situation_id or 0}:{stage[:40]}:{actual_sent_at.isoformat()}"
        ),
    )
    profile = session.scalar(
        select(TenantStrategyProfile).where(TenantStrategyProfile.contract_id == lease_id).limit(1)
    )
    if profile:
        profile.reminder_stage = stage[:40]
        profile.active_payment_situation = str(payment_situation_id or "")
        if previous:
            profile.soft_reminder_effectiveness = max(
                0.0,
                float(profile.soft_reminder_effectiveness or 0) - 0.1,
            )
        profile.updated_at = utc_now()
    return item


def update_latest_reminder_outcome(
    session: Session,
    *,
    lease_id: int,
    outcome: str,
    at: datetime | None = None,
) -> ReminderOutcome | None:
    at = at or utc_now()
    item = session.scalar(
        select(ReminderOutcome)
        .where(
            ReminderOutcome.contract_id == lease_id,
            ReminderOutcome.outcome.in_({"delivered", "ignored", "replied", "promised"}),
        )
        .order_by(ReminderOutcome.sent_at.desc(), ReminderOutcome.id.desc())
        .limit(1)
    )
    if not item:
        return None
    item.outcome = outcome
    item.outcome_at = at
    lease = session.get(Lease, lease_id)
    profile = tenant_strategy(session, lease) if lease else None
    if profile:
        hours = max(0, int((at - item.sent_at).total_seconds() // 3600))
        profile.response_speed_bucket = "fast" if hours < 6 else "same_day" if hours < 24 else "slow"
        if outcome in {"replied", "promised", "paid"} and item.stage in {"soft_overdue", "due", "pre_due"}:
            profile.soft_reminder_effectiveness = min(1.0, float(profile.soft_reminder_effectiveness or 0) + 0.2)
        elif outcome == "ignored":
            profile.soft_reminder_effectiveness = max(0.0, float(profile.soft_reminder_effectiveness or 0) - 0.1)
        if outcome == "promised":
            profile.active_payment_situation = "promised"
        profile.last_strategy_change = at
        profile.updated_at = at
    return item


def record_broken_promise(session: Session, *, lease_id: int) -> TenantStrategyProfile | None:
    lease = session.get(Lease, lease_id)
    if not lease:
        return None
    profile = tenant_strategy(session, lease)
    profile.broken_promises_count = int(profile.broken_promises_count or 0) + 1
    profile.reminder_stage = "broken_promise"
    profile.last_strategy_change = utc_now()
    return profile


def reminder_stage(
    *,
    due_date: date,
    today: date,
    promise_date: date | None = None,
) -> str:
    if promise_date and today > promise_date:
        return "broken_promise"
    delta = (today - due_date).days
    if delta < 0:
        return "pre_due"
    if delta == 0:
        return "due"
    if delta <= 2:
        return "soft_overdue"
    if delta <= 5:
        return "repeat_overdue"
    return "owner_escalation"


def reminder_allowed(
    session: Session,
    *,
    situation: PaymentSituation,
    now: datetime,
    quiet_start: time = time(hour=20),
    quiet_end: time = time(hour=10),
) -> tuple[bool, str]:
    current_time = now.time()
    in_quiet_hours = current_time >= quiet_start or current_time < quiet_end
    if in_quiet_hours:
        return False, "quiet_hours"
    if situation.paused_until and situation.paused_until >= now.date():
        return False, "active_deferral"
    if situation.mode == "manual" or situation.status in {"manual", "ignored", "receipt_pending_review", "question"}:
        return False, "situation_suppressed"
    duplicate = session.scalar(
        select(ReminderOutcome.id)
        .where(
            ReminderOutcome.contract_id == situation.lease_id,
            ReminderOutcome.sent_at >= datetime.combine(now.date(), time.min),
            ReminderOutcome.outcome.in_({"delivered", "replied", "promised", "paid"}),
        )
        .limit(1)
    )
    if duplicate:
        return False, "already_sent_today"
    return True, "allowed"
