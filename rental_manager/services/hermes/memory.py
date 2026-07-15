from __future__ import annotations

from datetime import date, datetime, time, timedelta
import json
import re
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from rental_manager.models import (
    AiConversation,
    AiMessage,
    ConversationSummary,
    OperationalCase,
    OwnerCommitment,
    OwnerPreference,
    utc_now,
)
from rental_manager.services.hermes.cases import OPEN_CASE_STATUSES, sync_case_memory
from rental_manager.services.hermes.events import emit_domain_event, stable_hash


def _next_weekday(value: date, weekday: int) -> date:
    days = (weekday - value.weekday()) % 7
    return value + timedelta(days=days or 7)


def parse_relative_due(text: str, *, now: datetime | None = None) -> datetime | None:
    now = now or datetime.now()
    lowered = re.sub(r"\s+", " ", (text or "").lower())
    if "сегодня" in lowered:
        return datetime.combine(now.date(), time(hour=18))
    if "завтра" in lowered:
        return datetime.combine(now.date() + timedelta(days=1), time(hour=10))
    after = re.search(r"через\s+(\d{1,3})\s*(?:дн|дня|день|дней)", lowered)
    if after:
        return datetime.combine(now.date() + timedelta(days=int(after.group(1))), time(hour=10))
    weekday_names = {
        "понедельник": 0,
        "понедельника": 0,
        "вторник": 1,
        "вторника": 1,
        "среду": 2,
        "среды": 2,
        "четверг": 3,
        "четверга": 3,
        "пятницу": 4,
        "пятницы": 4,
        "субботу": 5,
        "субботы": 5,
        "воскресенье": 6,
        "воскресенья": 6,
    }
    for token, weekday in weekday_names.items():
        if token in lowered:
            return datetime.combine(_next_weekday(now.date(), weekday), time(hour=10))
    iso = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", lowered)
    if iso:
        try:
            return datetime.combine(date.fromisoformat(iso.group(1)), time(hour=10))
        except ValueError:
            return None
    return None


def next_briefing_at(*, now: datetime | None = None, briefing_time: str = "10:00") -> datetime:
    now = now or datetime.now()
    try:
        hour, minute = (int(part) for part in briefing_time.split(":", 1))
        configured = time(hour=hour, minute=minute)
    except (TypeError, ValueError):
        configured = time(hour=10)
    candidate = datetime.combine(now.date(), configured)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def _preference_spec(text: str, *, case: OperationalCase | None = None, now: datetime | None = None) -> dict[str, Any] | None:
    now = now or datetime.now()
    lowered = re.sub(r"\s+", " ", (text or "").strip().lower())
    if not lowered:
        return None
    once = bool(re.search(r"\b(в следующий раз|в следующем аудите|в следующей сводке)\b", lowered))
    mode = "once" if once else "persistent"
    scope = "daily_briefing" if re.search(r"аудит|сводк", lowered) else "global"
    key = ""
    value: dict[str, Any] = {}
    if re.search(r"не (?:упоминай|показывай).{0,40}расход", lowered):
        key = "exclude_categories"
        value = {"categories": ["expense_compensation"]}
    elif re.search(r"(?:всегда )?группируй.{0,30}объект", lowered):
        key = "group_by_property"
        value = {"enabled": True}
    elif re.search(r"не используй.{0,20}эмодзи|без эмодзи", lowered):
        key = "use_emoji"
        value = {"enabled": False}
    elif re.search(r"показывай.{0,20}(?:общую|итоговую) сумму|сначала.{0,20}сумм", lowered):
        key = "total_first"
        value = {"enabled": True}
    elif re.search(r"не напоминай.{0,50}до ", lowered):
        key = "suppress_reminders"
        valid_until = parse_relative_due(lowered, now=now)
        if not valid_until:
            return None
        mode = "until_date"
        value = {"case_id": case.id if case else None}
        scope = f"property:{case.property_id}" if case and case.property_id else "global"
        return {
            "scope": scope,
            "key": key,
            "value": value,
            "mode": mode,
            "valid_until": valid_until,
        }
    else:
        return None
    return {"scope": scope, "key": key, "value": value, "mode": mode, "valid_until": None}


def capture_owner_preference(
    session: Session,
    text: str,
    *,
    case: OperationalCase | None = None,
    source: str = "owner_explicit",
    now: datetime | None = None,
) -> OwnerPreference | None:
    spec = _preference_spec(text, case=case, now=now)
    if not spec:
        return None
    query = select(OwnerPreference).where(
        OwnerPreference.scope == spec["scope"],
        OwnerPreference.key == spec["key"],
        OwnerPreference.mode == spec["mode"],
        OwnerPreference.enabled.is_(True),
    )
    existing = session.scalar(query.order_by(OwnerPreference.id.desc()).limit(1))
    if existing:
        existing.value_json = json.dumps(spec["value"], ensure_ascii=False, sort_keys=True)
        existing.valid_until = spec["valid_until"]
        existing.consumed_at = None
        existing.updated_at = utc_now()
        return existing
    preference = OwnerPreference(
        scope=spec["scope"],
        key=spec["key"],
        value_json=json.dumps(spec["value"], ensure_ascii=False, sort_keys=True),
        source=source,
        mode=spec["mode"],
        valid_until=spec["valid_until"],
        enabled=True,
    )
    session.add(preference)
    session.flush()
    emit_domain_event(
        session,
        "owner_preference_saved",
        entity_type="OwnerPreference",
        entity_id=preference.id,
        property_id=case.property_id if case else None,
        payload={"scope": preference.scope, "key": preference.key, "mode": preference.mode},
    )
    return preference


def applicable_preferences(
    session: Session,
    *,
    scopes: list[str],
    now: datetime | None = None,
) -> list[OwnerPreference]:
    now = now or utc_now()
    rows = session.scalars(
        select(OwnerPreference)
        .where(
            OwnerPreference.enabled.is_(True),
            OwnerPreference.scope.in_(list(dict.fromkeys(["global", *scopes]))),
            or_(OwnerPreference.valid_until.is_(None), OwnerPreference.valid_until >= now),
            OwnerPreference.consumed_at.is_(None),
        )
        .order_by(OwnerPreference.scope, OwnerPreference.created_at, OwnerPreference.id)
    ).all()
    return list(rows)


def preference_value(preference: OwnerPreference) -> dict[str, Any]:
    try:
        parsed = json.loads(preference.value_json or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def consume_one_shot_preferences(preferences: list[OwnerPreference]) -> None:
    now = utc_now()
    for preference in preferences:
        if preference.mode != "once" or preference.consumed_at:
            continue
        preference.consumed_at = now
        preference.enabled = False
        preference.updated_at = now


COMMITMENT_PATTERNS = (
    r"\bя разберусь\b",
    r"\bпроверю\b",
    r"\bнапомни (?:мне )?через\b",
    r"\bэтим занимается мастер\b",
    r"\bпока не трогай\b",
    r"\bя уже написал(?:а)? жильцу\b",
)


def is_commitment_phrase(text: str) -> bool:
    lowered = (text or "").lower()
    return any(re.search(pattern, lowered) for pattern in COMMITMENT_PATTERNS)


def create_owner_commitment(
    session: Session,
    *,
    case: OperationalCase | None,
    text: str,
    message_id: int | None = None,
    now: datetime | None = None,
    briefing_time: str = "10:00",
) -> OwnerCommitment:
    now = now or datetime.now()
    due_at = parse_relative_due(text, now=now) or next_briefing_at(now=now, briefing_time=briefing_time)
    existing = session.scalar(
        select(OwnerCommitment)
        .where(
            OwnerCommitment.case_id == (case.id if case else None),
            OwnerCommitment.status.in_({"active", "overdue"}),
        )
        .order_by(OwnerCommitment.id.desc())
        .limit(1)
    )
    if existing:
        existing.description = text.strip()[:1000]
        existing.due_at = due_at
        existing.status = "active"
        existing.updated_at = utc_now()
        commitment = existing
    else:
        commitment = OwnerCommitment(
            case_id=case.id if case else None,
            description=text.strip()[:1000],
            status="active",
            due_at=due_at,
            created_from_message_id=message_id,
            completion_condition="Владелец подтверждает результат или состояние кейса становится resolved",
            metadata_json=json.dumps({"source": "owner_message"}, ensure_ascii=False),
        )
        session.add(commitment)
        session.flush()
    if case:
        case.status = "waiting_owner"
        case.waiting_for = "owner"
        case.assigned_actor = "owner"
        case.next_review_at = due_at
        case.updated_at = utc_now()
        sync_case_memory(session, case)
    emit_domain_event(
        session,
        "owner_commitment_created",
        entity_type="OwnerCommitment",
        entity_id=commitment.id,
        property_id=case.property_id if case else None,
        apartment_id=case.apartment_id if case else None,
        tenant_id=case.tenant_id if case else None,
        contract_id=case.contract_id if case else None,
        payload={"case_id": case.id if case else None, "due_at": due_at.isoformat(), "description": commitment.description},
    )
    return commitment


def reconcile_owner_commitments(session: Session, *, now: datetime | None = None) -> list[OwnerCommitment]:
    now = now or utc_now()
    rows = session.scalars(
        select(OwnerCommitment)
        .where(OwnerCommitment.status.in_({"active", "overdue"}))
        .order_by(OwnerCommitment.due_at, OwnerCommitment.id)
    ).all()
    for commitment in rows:
        case = session.get(OperationalCase, commitment.case_id) if commitment.case_id else None
        if case and case.status not in OPEN_CASE_STATUSES:
            commitment.status = "completed"
            commitment.completed_at = now
            commitment.updated_at = now
            emit_domain_event(
                session,
                "owner_commitment_completed",
                entity_type="OwnerCommitment",
                entity_id=commitment.id,
                payload={"case_id": commitment.case_id, "reason": "case resolved"},
            )
        elif commitment.due_at and commitment.due_at <= now and commitment.status != "overdue":
            commitment.status = "overdue"
            commitment.updated_at = now
            emit_domain_event(
                session,
                "owner_commitment_overdue",
                entity_type="OwnerCommitment",
                entity_id=commitment.id,
                payload={"case_id": commitment.case_id, "due_at": commitment.due_at.isoformat()},
            )
    return list(rows)


def complete_owner_commitment(session: Session, commitment_id: int) -> OwnerCommitment:
    commitment = session.get(OwnerCommitment, commitment_id)
    if not commitment:
        raise ValueError("Обязательство не найдено")
    if commitment.status == "completed":
        return commitment
    commitment.status = "completed"
    commitment.completed_at = utc_now()
    commitment.updated_at = utc_now()
    return commitment


def postpone_owner_commitment(session: Session, commitment_id: int, *, days: int = 1) -> OwnerCommitment:
    commitment = session.get(OwnerCommitment, commitment_id)
    if not commitment:
        raise ValueError("Обязательство не найдено")
    commitment.status = "active"
    commitment.due_at = datetime.combine(date.today() + timedelta(days=max(1, days)), time(hour=10))
    commitment.updated_at = utc_now()
    case = session.get(OperationalCase, commitment.case_id) if commitment.case_id else None
    if case:
        case.next_review_at = commitment.due_at
        case.status = "waiting_owner"
    return commitment


def update_conversation_summary(session: Session, conversation: AiConversation, *, message_limit: int = 8) -> ConversationSummary:
    messages = session.scalars(
        select(AiMessage)
        .where(AiMessage.conversation_id == conversation.id, AiMessage.role.in_({"user", "assistant"}))
        .order_by(AiMessage.created_at.desc(), AiMessage.id.desc())
        .limit(max(1, message_limit))
    ).all()
    messages = list(reversed(messages))
    compact_parts: list[str] = []
    for row in messages:
        message_text = re.sub(r"\s+", " ", row.text or "").strip()[:240]
        if message_text:
            author = "Владелец" if row.role == "user" else "Hermes"
            compact_parts.append(f"{author}: {message_text}")
    compact = " | ".join(compact_parts)[:1600]
    state_hash = stable_hash([(row.id, row.role, row.text) for row in messages])
    summary = session.scalar(
        select(ConversationSummary).where(ConversationSummary.conversation_id == conversation.id).limit(1)
    )
    if not summary:
        summary = ConversationSummary(conversation_id=conversation.id)
        session.add(summary)
    if summary.state_hash != state_hash:
        summary.summary = compact
        summary.last_message_id = messages[-1].id if messages else None
        summary.state_hash = state_hash
        summary.updated_at = utc_now()
    return summary
