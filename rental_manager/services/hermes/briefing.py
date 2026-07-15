from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from rental_manager.models import (
    OperationalCase,
    OwnerBriefing,
    OwnerBriefingItem,
    OwnerCommitment,
    OwnerPreference,
    RentalObject,
    utc_now,
)
from rental_manager.services.hermes.cases import (
    case_label,
    reconcile_operational_cases,
)
from rental_manager.services.hermes.events import stable_hash
from rental_manager.services.hermes.memory import (
    applicable_preferences,
    consume_one_shot_preferences,
    preference_value,
    reconcile_owner_commitments,
)


BRIEFING_MAX_CASES = 3
BRIEFING_MAX_CHARS = 800


@dataclass(frozen=True)
class BriefingBuild:
    briefing: OwnerBriefing | None
    cases: list[OperationalCase]
    preferences: list[OwnerPreference]
    remaining_count: int
    text: str
    selection_reasons: dict[int, str]


def _excluded_types(preferences: list[OwnerPreference]) -> set[str]:
    result: set[str] = set()
    for preference in preferences:
        if preference.key != "exclude_categories":
            continue
        result.update(str(value) for value in preference_value(preference).get("categories", []))
    return result


def _preference_enabled(preferences: list[OwnerPreference], key: str, default: bool = False) -> bool:
    matching = [preference for preference in preferences if preference.key == key]
    if not matching:
        return default
    return bool(preference_value(matching[-1]).get("enabled", default))


def _last_included_state_hash(session: Session, case_id: int) -> str:
    item = session.scalar(
        select(OwnerBriefingItem)
        .where(OwnerBriefingItem.case_id == case_id)
        .order_by(OwnerBriefingItem.id.desc())
        .limit(1)
    )
    return item.state_hash if item else ""


def _due_commitments(session: Session, now: datetime) -> dict[int, OwnerCommitment]:
    rows = session.scalars(
        select(OwnerCommitment)
        .where(
            OwnerCommitment.status.in_({"active", "overdue"}),
            OwnerCommitment.case_id.is_not(None),
            OwnerCommitment.due_at.is_not(None),
            OwnerCommitment.due_at <= now,
        )
        .order_by(OwnerCommitment.due_at, OwnerCommitment.id)
    ).all()
    return {int(row.case_id): row for row in rows if row.case_id}


def _candidate_reason(
    item: OperationalCase,
    *,
    last_hash: str,
    commitment: OwnerCommitment | None,
) -> str:
    if commitment:
        return "наступил срок обязательства владельца"
    if not last_hash:
        return "новый кейс"
    if last_hash != item.state_hash:
        return "состояние изменилось"
    if item.waiting_for == "owner":
        return "требуется решение владельца"
    return f"priority {item.priority_score:.1f}"


def _case_line(session: Session, item: OperationalCase, commitment: OwnerCommitment | None = None) -> str:
    label = case_label(session, item)
    if commitment:
        return f"{label} — получилось разобраться?"
    summary = item.compact_summary.rstrip(". ")
    return f"{label} — {summary}."


def _render_text(
    session: Session,
    selected: list[OperationalCase],
    *,
    remaining: int,
    preferences: list[OwnerPreference],
    commitments: dict[int, OwnerCommitment],
    max_chars: int,
) -> str:
    total_first = _preference_enabled(preferences, "total_first")
    total = round(sum(float(item.amount_total or 0) for item in selected), 2)
    lines = [f"На сегодня {len(selected)} вопроса"]
    if total_first and total > 0:
        rendered = f"{total:,.2f}".replace(",", " ").replace(".00", "")
        lines.append(f"Общая сумма: {rendered} ₽.")
    lines.append("")
    groups: dict[int | None, list[OperationalCase]] = {}
    for item in selected:
        groups.setdefault(item.property_id, []).append(item)
    for property_id, cases in groups.items():
        if len(cases) == 1:
            item = cases[0]
            lines.append(_case_line(session, item, commitments.get(item.id)))
            continue
        rental_object = session.get(RentalObject, property_id) if property_id else None
        group_label = (rental_object.short_code or rental_object.name) if rental_object else "Без объекта"
        summaries = []
        for item in cases:
            if commitments.get(item.id):
                summaries.append(f"{case_label(session, item)}: получилось разобраться?")
            else:
                summaries.append(f"{case_label(session, item)}: {item.compact_summary.rstrip('. ')}")
        lines.append(f"{group_label} — " + "; ".join(summaries) + ".")
    if remaining:
        lines.extend(["", f"Ещё {remaining} ситуаций находятся под контролем."])
    text = "\n".join(lines).strip()
    if len(text) <= max_chars:
        return text
    suffix = "…"
    return text[: max_chars - len(suffix)].rstrip(" ,;.") + suffix


def build_owner_briefing(
    session: Session,
    *,
    today: date | None = None,
    persist: bool = True,
    force: bool = False,
    max_chars: int = BRIEFING_MAX_CHARS,
) -> BriefingBuild:
    today = today or date.today()
    max_chars = min(BRIEFING_MAX_CHARS, max(200, int(max_chars)))
    now = utc_now()
    if not force:
        sent = session.scalar(
            select(OwnerBriefing)
            .where(OwnerBriefing.briefing_date == today, OwnerBriefing.status == "sent")
            .order_by(OwnerBriefing.id.desc())
            .limit(1)
        )
        if sent:
            rows = session.scalars(
                select(OwnerBriefingItem)
                .where(OwnerBriefingItem.briefing_id == sent.id)
                .order_by(OwnerBriefingItem.position)
            ).all()
            cases = [session.get(OperationalCase, row.case_id) for row in rows]
            selected = [item for item in cases if item]
            return BriefingBuild(sent, selected, [], 0, sent.text, {})

    reconcile_owner_commitments(session, now=now)
    open_cases = reconcile_operational_cases(session, today)
    scopes = ["daily_briefing", *[f"property:{item.property_id}" for item in open_cases if item.property_id]]
    preferences = applicable_preferences(session, scopes=scopes, now=now)
    excluded = _excluded_types(preferences)
    commitments = _due_commitments(session, now)
    candidates: list[tuple[float, OperationalCase, str]] = []
    for item in open_cases:
        if item.case_type in excluded:
            continue
        if item.suppression_until and item.suppression_until > now:
            continue
        last_hash = _last_included_state_hash(session, item.id)
        unchanged_waiting = (
            item.status in {"waiting_owner", "waiting_tenant", "auto_monitoring"}
            and last_hash == item.state_hash
            and item.id not in commitments
            and (not item.next_review_at or item.next_review_at > now)
        )
        if unchanged_waiting:
            continue
        reason = _candidate_reason(item, last_hash=last_hash, commitment=commitments.get(item.id))
        score = float(item.priority_score or 0) + (50 if item.id in commitments else 0)
        candidates.append((score, item, reason))
    candidates.sort(key=lambda value: (-value[0], value[1].first_seen_at, value[1].id))
    selected_rows = candidates[:BRIEFING_MAX_CASES]
    selected = [item for _score, item, _reason in selected_rows]
    remaining = max(0, len(open_cases) - len(selected))
    reasons = {item.id: reason for _score, item, reason in selected_rows}
    if not selected:
        return BriefingBuild(None, [], preferences, remaining, "", {})
    text = _render_text(
        session,
        selected,
        remaining=remaining,
        preferences=preferences,
        commitments=commitments,
        max_chars=max_chars,
    )
    if not persist:
        return BriefingBuild(None, selected, preferences, remaining, text, reasons)
    briefing_hash = stable_hash([(item.id, item.state_hash) for item in selected])
    briefing = OwnerBriefing(
        briefing_date=today,
        status="draft",
        text=text,
        state_hash=briefing_hash,
        preference_ids_json=json.dumps([item.id for item in preferences]),
    )
    session.add(briefing)
    session.flush()
    for position, item in enumerate(selected, start=1):
        session.add(
            OwnerBriefingItem(
                briefing_id=briefing.id,
                case_id=item.id,
                position=position,
                selection_reason=reasons[item.id],
                state_hash=item.state_hash,
            )
        )
    session.flush()
    return BriefingBuild(briefing, selected, preferences, remaining, text, reasons)


def briefing_keyboard(session: Session, build: BriefingBuild) -> dict[str, Any]:
    rows: list[list[dict[str, str]]] = []
    commitments = _due_commitments(session, utc_now())
    for item in build.cases:
        commitment = commitments.get(item.id)
        if commitment:
            rows.append(
                [
                    {"text": "Да, решено", "callback_data": f"ai:commitment:{commitment.id}:done"},
                    {"text": "Перенести", "callback_data": f"ai:commitment:{commitment.id}:postpone"},
                ]
            )
        rows.append(
            [
                {"text": f"Подробнее · {case_label(session, item)}", "callback_data": f"ai:case:{item.id}:details"},
            ]
        )
    if build.briefing and build.remaining_count:
        rows.append(
            [{"text": "Показать остальные", "callback_data": f"ai:briefing:{build.briefing.id}:remaining"}]
        )
    if build.briefing:
        rows.append([{"text": "Отложить сводку", "callback_data": f"ai:briefing:{build.briefing.id}:snooze"}])
    return {"inline_keyboard": rows}


def mark_briefing_sent(
    build: BriefingBuild,
    *,
    telegram_message_id: int | None = None,
) -> OwnerBriefing | None:
    briefing = build.briefing
    if not briefing:
        return None
    briefing.status = "sent"
    briefing.sent_at = utc_now()
    briefing.telegram_message_id = telegram_message_id
    consume_one_shot_preferences(build.preferences)
    return briefing


def briefing_preview(
    session: Session,
    *,
    today: date | None = None,
    max_chars: int = BRIEFING_MAX_CHARS,
) -> dict[str, Any]:
    max_chars = min(BRIEFING_MAX_CHARS, max(200, int(max_chars)))
    build = build_owner_briefing(session, today=today, persist=False, force=True, max_chars=max_chars)
    return {
        "text": build.text,
        "length": len(build.text),
        "limit": max_chars,
        "case_ids": [item.id for item in build.cases],
        "remaining_count": build.remaining_count,
        "selection_reasons": build.selection_reasons,
        "preferences": [
            {"id": item.id, "scope": item.scope, "key": item.key, "mode": item.mode}
            for item in build.preferences
        ],
    }


def snooze_briefing(session: Session, briefing_id: int, *, hours: int = 3) -> OwnerBriefing:
    briefing = session.get(OwnerBriefing, briefing_id)
    if not briefing:
        raise ValueError("Сводка не найдена")
    briefing.status = "snoozed"
    briefing.snoozed_until = utc_now() + timedelta(hours=max(1, hours))
    return briefing
