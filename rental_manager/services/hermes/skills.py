from __future__ import annotations

from datetime import datetime
import json
import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from rental_manager.models import AiSkill, AiSkillRun, DomainEvent, OperationalCase, utc_now
from rental_manager.services.hermes.safety import ACTION_SAFETY_REGISTRY


def _json_list(value: str) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _skill_blueprint(text: str) -> dict[str, Any]:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    lowered = normalized.lower()
    if "обещал" in lowered and "оплат" in lowered and re.search(r"срок|прош[её]л|ист[её]к", lowered):
        return {
            "name": "Контроль обещания оплаты",
            "description": normalized[:1000],
            "goal": "Мягко проверить нарушенное обещание и вовремя сообщить владельцу",
            "trigger_type": "domain_event",
            "trigger_config": {"event_types": ["payment_promise_expired", "owner_commitment_overdue"]},
            "preconditions": [
                {"field": "case.case_type", "operator": "eq", "value": "broken_payment_promise"},
                {"field": "case.status", "operator": "in", "value": ["new", "active", "waiting_tenant"]},
            ],
            "steps": [
                {"tool": "send_approved_template_reminder", "template": "broken_promise_soft"},
                {"wait_hours": 24},
                {"condition": "no_tenant_response", "tool": "notify_owner"},
            ],
            "allowed_tools": ["send_approved_template_reminder", "notify_owner"],
            "autonomous_tools": ["send_approved_template_reminder", "notify_owner"],
            "confirmation_required_tools": [],
            "success_metric": {"outcomes": ["replied", "promised", "paid", "escalated"]},
        }
    return {
        "name": (normalized[:80] or "Новый навык").rstrip(".:-"),
        "description": normalized[:1000],
        "goal": normalized[:1000],
        "trigger_type": "manual",
        "trigger_config": {},
        "preconditions": [],
        "steps": [{"tool": "create_message_draft"}],
        "allowed_tools": ["create_message_draft"],
        "autonomous_tools": [],
        "confirmation_required_tools": [],
        "success_metric": {"outcomes": ["owner_accepted"]},
    }


def create_skill_draft(
    session: Session,
    text: str,
    *,
    scope: str = "global",
    created_by: str = "owner",
) -> AiSkill:
    blueprint = _skill_blueprint(text)
    latest = session.scalar(
        select(func.max(AiSkill.version)).where(AiSkill.name == blueprint["name"])
    )
    version = int(latest or 0) + 1
    errors = ACTION_SAFETY_REGISTRY.validate_skill(blueprint)
    status = "draft"
    skill = AiSkill(
        name=blueprint["name"],
        description=blueprint["description"],
        goal=blueprint["goal"],
        trigger_type=blueprint["trigger_type"],
        trigger_config_json=json.dumps(blueprint["trigger_config"], ensure_ascii=False, sort_keys=True),
        preconditions_json=json.dumps(blueprint["preconditions"], ensure_ascii=False, sort_keys=True),
        steps_json=json.dumps(blueprint["steps"], ensure_ascii=False, sort_keys=True),
        allowed_tools_json=json.dumps(blueprint["allowed_tools"], ensure_ascii=False),
        autonomous_tools_json=json.dumps(blueprint["autonomous_tools"], ensure_ascii=False),
        confirmation_required_tools_json=json.dumps(blueprint["confirmation_required_tools"], ensure_ascii=False),
        success_metric_json=json.dumps(blueprint["success_metric"], ensure_ascii=False, sort_keys=True),
        scope=scope,
        version=version,
        status=status,
        created_by=created_by,
    )
    if errors:
        skill.description = (skill.description + "\nОграничения: " + "; ".join(errors))[:2000]
    session.add(skill)
    session.flush()
    return skill


def skill_card(skill: AiSkill) -> str:
    tools = _json_list(skill.allowed_tools_json)
    autonomous = _json_list(skill.autonomous_tools_json)
    return (
        f"Навык: {skill.name} · v{skill.version}\n"
        f"Цель: {skill.goal[:400]}\n"
        f"Триггер: {skill.trigger_type}\n"
        f"Tools: {', '.join(str(item) for item in tools) or 'нет'}\n"
        f"Автономно: {', '.join(str(item) for item in autonomous) or 'нет'}\n"
        f"Статус: {skill.status}"
    )[:1000]


def validate_skill(skill: AiSkill) -> list[str]:
    return ACTION_SAFETY_REGISTRY.validate_skill(
        {
            "allowed_tools_json": skill.allowed_tools_json,
            "autonomous_tools_json": skill.autonomous_tools_json,
            "confirmation_required_tools_json": skill.confirmation_required_tools_json,
        }
    )


def activate_skill(session: Session, skill_id: int) -> AiSkill:
    skill = session.get(AiSkill, skill_id)
    if not skill:
        raise ValueError("Навык не найден")
    errors = validate_skill(skill)
    if errors:
        raise ValueError("; ".join(errors))
    active_versions = session.scalars(
        select(AiSkill).where(AiSkill.name == skill.name, AiSkill.status == "active", AiSkill.id != skill.id)
    ).all()
    for old in active_versions:
        old.status = "archived"
        old.updated_at = utc_now()
    skill.status = "active"
    skill.updated_at = utc_now()
    return skill


def disable_skill(session: Session, skill_id: int) -> AiSkill:
    skill = session.get(AiSkill, skill_id)
    if not skill:
        raise ValueError("Навык не найден")
    skill.status = "disabled"
    skill.updated_at = utc_now()
    return skill


def version_skill(session: Session, skill_id: int, changes: dict[str, Any]) -> AiSkill:
    current = session.get(AiSkill, skill_id)
    if not current:
        raise ValueError("Навык не найден")
    allowed_changes = {
        "description",
        "goal",
        "trigger_type",
        "trigger_config_json",
        "preconditions_json",
        "steps_json",
        "allowed_tools_json",
        "autonomous_tools_json",
        "confirmation_required_tools_json",
        "success_metric_json",
        "scope",
    }
    values = {key: getattr(current, key) for key in allowed_changes}
    values.update({key: value for key, value in changes.items() if key in allowed_changes})
    latest = session.scalar(select(func.max(AiSkill.version)).where(AiSkill.name == current.name))
    updated = AiSkill(
        name=current.name,
        version=int(latest or current.version) + 1,
        status="draft",
        created_by="owner",
        previous_version_id=current.id,
        **values,
    )
    session.add(updated)
    session.flush()
    return updated


def rollback_skill(session: Session, skill_id: int) -> AiSkill:
    current = session.get(AiSkill, skill_id)
    if not current or not current.previous_version_id:
        raise ValueError("Предыдущая версия навыка не найдена")
    previous = session.get(AiSkill, current.previous_version_id)
    if not previous:
        raise ValueError("Предыдущая версия навыка не найдена")
    current.status = "archived"
    previous.status = "active"
    current.updated_at = previous.updated_at = utc_now()
    return previous


def dry_run_skill(skill: AiSkill, *, case: OperationalCase | None = None) -> dict[str, Any]:
    tools = _json_list(skill.allowed_tools_json)
    return {
        "skill_id": skill.id,
        "status": skill.status,
        "case_id": case.id if case else None,
        "trigger_matches": bool(skill.trigger_type == "manual" or case),
        "tools": [
            {
                "tool": tool,
                "safety_level": ACTION_SAFETY_REGISTRY.classify(str(tool), owner_level_one_enabled=True).level,
                "would_execute": False,
            }
            for tool in tools
        ],
        "note": "Dry-run не меняет данные и не отправляет сообщения.",
    }


def evaluate_event_skills(
    session: Session,
    event: DomainEvent,
    *,
    case: OperationalCase | None = None,
) -> list[AiSkillRun]:
    skills = session.scalars(select(AiSkill).where(AiSkill.status == "active", AiSkill.trigger_type == "domain_event")).all()
    runs: list[AiSkillRun] = []
    for skill in skills:
        try:
            config = json.loads(skill.trigger_config_json or "{}")
        except json.JSONDecodeError:
            config = {}
        if event.event_type not in config.get("event_types", []):
            continue
        tools = _json_list(skill.allowed_tools_json)
        run = AiSkillRun(
            skill_id=skill.id,
            case_id=case.id if case else None,
            trigger_event_id=event.id,
            status="proposed",
            selected_tools_json=json.dumps(tools, ensure_ascii=False),
            result_json=json.dumps({"reason": "skill matched; executor applies policy separately"}, ensure_ascii=False),
            completed_at=datetime.utcnow(),
        )
        session.add(run)
        runs.append(run)
    return runs
