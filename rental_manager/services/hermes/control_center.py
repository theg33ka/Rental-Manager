from __future__ import annotations

from datetime import date, datetime, timedelta
import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from rental_manager.models import (
    AgentActionProposal,
    AiSkill,
    DomainEvent,
    HermesAgentRun,
    OperationalCase,
    OwnerCommitment,
    OwnerPreference,
    TenantStrategyProfile,
    utc_now,
)
from rental_manager.services.hermes.cases import case_snapshot, reconcile_operational_cases
from rental_manager.services.hermes.runtime import usage_summary


def _json(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (json.JSONDecodeError, TypeError):
        return fallback


def overview(
    session: Session,
    *,
    enabled: bool,
    monthly_budget_rub: float = 0,
) -> dict[str, Any]:
    cases = reconcile_operational_cases(session)
    statuses: dict[str, int] = {}
    for item in cases:
        statuses[item.status] = statuses.get(item.status, 0) + 1
    usage = usage_summary(session)
    spent = float(usage["month_spent_rub"])
    budget_percent = round(spent / monthly_budget_rub * 100, 1) if monthly_budget_rub > 0 else 0
    warnings = [threshold for threshold in (50, 80, 95) if budget_percent >= threshold]
    today_spent = float(
        session.scalar(
            select(func.coalesce(func.sum(HermesAgentRun.estimated_cost), 0)).where(
                HermesAgentRun.started_at >= datetime.combine(date.today(), datetime.min.time())
            )
        )
        or 0
    )
    return {
        "enabled": enabled,
        "active_cases": len(cases),
        "waiting_owner": statuses.get("waiting_owner", 0),
        "waiting_tenant": statuses.get("waiting_tenant", 0),
        "auto_monitoring": statuses.get("auto_monitoring", 0),
        "scheduled_actions": int(
            session.scalar(select(func.count(OwnerCommitment.id)).where(OwnerCommitment.status.in_({"active", "overdue"})))
            or 0
        ),
        "pending_proposals": int(
            session.scalar(select(func.count(AgentActionProposal.id)).where(AgentActionProposal.status == "pending")) or 0
        ),
        "cost_today_rub": round(today_spent, 2),
        "cost_month_rub": spent,
        "monthly_forecast_rub": usage["month_forecast_rub"],
        "monthly_budget_rub": monthly_budget_rub,
        "budget_percent": budget_percent,
        "budget_warnings": warnings,
        "errors": int(
            session.scalar(
                select(func.count(HermesAgentRun.id)).where(
                    HermesAgentRun.status == "failed",
                    HermesAgentRun.started_at >= utc_now() - timedelta(days=7),
                )
            )
            or 0
        ),
    }


def list_cases(
    session: Session,
    *,
    status: str = "",
    severity: str = "",
    property_id: int | None = None,
    refresh: bool = True,
) -> list[dict[str, Any]]:
    if refresh:
        reconcile_operational_cases(session)
    query = select(OperationalCase)
    if status:
        query = query.where(OperationalCase.status == status)
    if severity:
        query = query.where(OperationalCase.severity == severity)
    if property_id:
        query = query.where(OperationalCase.property_id == property_id)
    rows = session.scalars(
        query.order_by(OperationalCase.priority_score.desc(), OperationalCase.last_changed_at.desc(), OperationalCase.id)
    ).all()
    return [case_snapshot(session, item) for item in rows]


def case_details(session: Session, case_id: int) -> dict[str, Any] | None:
    item = session.get(OperationalCase, case_id)
    if not item:
        return None
    snapshot = case_snapshot(session, item)
    events = session.scalars(
        select(DomainEvent)
        .where(
            (DomainEvent.contract_id == item.contract_id if item.contract_id else DomainEvent.id == -1)
            | (DomainEvent.entity_id == str(item.id)),
        )
        .order_by(DomainEvent.occurred_at.desc(), DomainEvent.id.desc())
        .limit(20)
    ).all()
    commitments = session.scalars(
        select(OwnerCommitment).where(OwnerCommitment.case_id == item.id).order_by(OwnerCommitment.created_at.desc())
    ).all()
    proposals = session.scalars(
        select(AgentActionProposal).where(AgentActionProposal.case_id == item.id).order_by(AgentActionProposal.id.desc())
    ).all()
    snapshot["history"] = [
        {
            "id": event.id,
            "event_type": event.event_type,
            "occurred_at": event.occurred_at.isoformat(),
            "actor": event.actor_type,
            "source": event.source,
            "payload": _json(event.payload_json, {}),
        }
        for event in events
    ]
    snapshot["commitments"] = [serialize_commitment(item) for item in commitments]
    snapshot["proposals"] = [serialize_proposal(item) for item in proposals]
    return snapshot


def serialize_commitment(item: OwnerCommitment) -> dict[str, Any]:
    return {
        "id": item.id,
        "case_id": item.case_id,
        "description": item.description,
        "status": item.status,
        "due_at": item.due_at.isoformat() if item.due_at else None,
        "completion_condition": item.completion_condition,
        "created_at": item.created_at.isoformat(),
        "completed_at": item.completed_at.isoformat() if item.completed_at else None,
        "metadata": _json(item.metadata_json, {}),
    }


def serialize_preference(item: OwnerPreference) -> dict[str, Any]:
    return {
        "id": item.id,
        "scope": item.scope,
        "key": item.key,
        "value": _json(item.value_json, {}),
        "source": item.source,
        "mode": item.mode,
        "valid_until": item.valid_until.isoformat() if item.valid_until else None,
        "consumed_at": item.consumed_at.isoformat() if item.consumed_at else None,
        "enabled": item.enabled,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


def serialize_skill(item: AiSkill) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "description": item.description,
        "goal": item.goal,
        "trigger_type": item.trigger_type,
        "trigger_config": _json(item.trigger_config_json, {}),
        "preconditions": _json(item.preconditions_json, []),
        "steps": _json(item.steps_json, []),
        "allowed_tools": _json(item.allowed_tools_json, []),
        "autonomous_tools": _json(item.autonomous_tools_json, []),
        "confirmation_required_tools": _json(item.confirmation_required_tools_json, []),
        "success_metric": _json(item.success_metric_json, {}),
        "scope": item.scope,
        "version": item.version,
        "status": item.status,
        "created_by": item.created_by,
        "previous_version_id": item.previous_version_id,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


def serialize_strategy(item: TenantStrategyProfile) -> dict[str, Any]:
    return {
        "id": item.id,
        "tenant_id": item.tenant_id,
        "contract_id": item.contract_id,
        "preferred_channel": item.preferred_channel,
        "preferred_contact_window": item.preferred_contact_window,
        "response_speed_bucket": item.response_speed_bucket,
        "reminder_stage": item.reminder_stage,
        "soft_reminder_effectiveness": item.soft_reminder_effectiveness,
        "broken_promises_count": item.broken_promises_count,
        "active_payment_situation": item.active_payment_situation,
        "escalation_threshold": item.escalation_threshold,
        "owner_only_topics": _json(item.owner_only_topics_json, []),
        "last_strategy_change": item.last_strategy_change.isoformat(),
        "strategy_source": item.strategy_source,
        "metadata": _json(item.metadata_json, {}),
    }


def serialize_proposal(item: AgentActionProposal) -> dict[str, Any]:
    return {
        "id": item.id,
        "case_id": item.case_id,
        "action_type": item.action_type,
        "status": item.status,
        "parameters": _json(item.payload_json, {}),
        "preview": item.preview_text,
        "result": item.result_text,
        "error": item.error_text,
        "requested_by": item.requested_by,
        "actor": item.confirmed_by,
        "safety_level": item.safety_level,
        "idempotency_key": item.idempotency_key,
        "group_key": item.group_key,
        "created_at": item.created_at.isoformat(),
        "expires_at": item.expires_at.isoformat() if item.expires_at else None,
        "confirmed_at": item.confirmed_at.isoformat() if item.confirmed_at else None,
        "executed_at": item.executed_at.isoformat() if item.executed_at else None,
    }


def serialize_run(item: HermesAgentRun, *, include_debug: bool = False) -> dict[str, Any]:
    result = {
        "id": item.id,
        "run_id": item.run_id,
        "feature": item.feature,
        "trigger": item.trigger,
        "model": item.model,
        "input_tokens": item.input_tokens,
        "output_tokens": item.output_tokens,
        "estimated_cost": item.estimated_cost,
        "selected_tools": _json(item.selected_tools_json, []),
        "proposal_ids": _json(item.proposal_ids_json, []),
        "execution_result": _json(item.execution_result_json, {}),
        "error": item.error_text,
        "status": item.status,
        "started_at": item.started_at.isoformat(),
        "completed_at": item.completed_at.isoformat() if item.completed_at else None,
    }
    if include_debug:
        result.update(
            {
                "context_manifest": _json(item.context_manifest_json, {}),
                "normalized_envelope": _json(item.normalized_envelope_json, {}),
                "state_hash": item.state_hash,
            }
        )
    return result


def control_center_snapshot(
    session: Session,
    *,
    enabled: bool,
    monthly_budget_rub: float = 0,
) -> dict[str, Any]:
    commitments = session.scalars(
        select(OwnerCommitment).order_by(OwnerCommitment.status, OwnerCommitment.due_at, OwnerCommitment.id)
    ).all()
    preferences = session.scalars(
        select(OwnerPreference).order_by(OwnerPreference.enabled.desc(), OwnerPreference.scope, OwnerPreference.id)
    ).all()
    skills = session.scalars(select(AiSkill).order_by(AiSkill.name, AiSkill.version.desc())).all()
    strategies = session.scalars(select(TenantStrategyProfile).order_by(TenantStrategyProfile.contract_id)).all()
    proposals = session.scalars(select(AgentActionProposal).order_by(AgentActionProposal.id.desc()).limit(100)).all()
    runs = session.scalars(select(HermesAgentRun).order_by(HermesAgentRun.id.desc()).limit(100)).all()
    return {
        "overview": overview(session, enabled=enabled, monthly_budget_rub=monthly_budget_rub),
        "cases": list_cases(session, refresh=False),
        "commitments": [serialize_commitment(item) for item in commitments],
        "preferences": [serialize_preference(item) for item in preferences],
        "skills": [serialize_skill(item) for item in skills],
        "tenant_strategies": [serialize_strategy(item) for item in strategies],
        "proposals": [serialize_proposal(item) for item in proposals],
        "usage": usage_summary(session),
        "runs": [serialize_run(item) for item in runs],
    }
