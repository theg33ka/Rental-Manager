from __future__ import annotations

from datetime import date, timedelta
import json
import re
from typing import Any
import uuid

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from rental_manager.models import (
    AgentActionProposal,
    AiConversation,
    AiFeatureUsageDaily,
    AiMessage,
    Apartment,
    ConversationSummary,
    HermesAgentRun,
    Lease,
    ManualDebt,
    OperationalCase,
    OwnerCommitment,
    PaymentSituation,
    RentCharge,
    UtilityBillLine,
    utc_now,
)
from rental_manager.services.hermes.cases import (
    case_snapshot,
    reconcile_operational_cases,
    resolve_case_alias,
)
from rental_manager.services.hermes.events import stable_hash
from rental_manager.services.hermes.memory import applicable_preferences, preference_value
from rental_manager.services.hermes.safety import ActionSafetyRegistry


FEATURE_OUTPUT_LIMITS = {
    "daily_briefing": 800,
    "case_details": 1200,
    "owner_chat": 800,
    "tenant_chat": 500,
    "skill_builder": 1000,
    "deep_audit": 1800,
    "key_test": 600,
}


TOOL_GROUPS = {
    "read_only_finance": ["read_finance", "search_entities"],
    "property_contract": ["read_property", "read_contract", "update_lease", "transfer_lease", "move_out"],
    "rent_payments": ["read_finance", "add_rent_payment", "create_manual_payment", "update_payment_receipt"],
    "utility": ["read_finance", "save_meter_reading", "calculate_utility_bill", "issue_utility_bill"],
    "messages": ["read_messages", "create_message_draft", "send_tenant_message", "broadcast_message"],
    "reminders": ["read_messages", "send_approved_template_reminder", "run_reminders"],
    "deferrals": ["read_finance", "defer_payment_situation", "defer_rent"],
    "expenses": ["read_finance", "create_expense", "compensate_expense"],
    "reports": ["read_finance", "accept_monthly_report"],
    "preferences": ["save_owner_preference"],
    "commitments": ["create_commitment", "schedule_case_review"],
    "skills": ["build_case_summary"],
    "system_settings": ["read_property"],
}


class ContextManifest(BaseModel):
    feature: str
    model: str
    selected_case_ids: list[int] = Field(default_factory=list)
    included_entities: dict[str, list[int | str]] = Field(default_factory=dict)
    included_messages_count: int = 0
    approximate_input_size: int = 0
    reason_for_llm_usage: str
    excluded_context: list[str] = Field(default_factory=list)
    output_limit: int
    selected_tools: list[str] = Field(default_factory=list)
    state_hash: str = ""


class OwnerIntent(BaseModel):
    group: str
    read_only: bool = True
    wants_detail: bool = False
    wants_action: bool = False
    wants_skill: bool = False
    wants_preference: bool = False
    wants_commitment: bool = False


class SelectedContext(BaseModel):
    text: str
    manifest: ContextManifest
    case_ids: list[int] = Field(default_factory=list)
    ambiguous_case_ids: list[int] = Field(default_factory=list)


class GroupedProposalPlan(BaseModel):
    matched: bool = False
    lease_ids: list[int] = Field(default_factory=list)
    ambiguous_lease_ids: list[int] = Field(default_factory=list)
    items: list[dict[str, Any]] = Field(default_factory=list)
    new_date: str = ""
    total_amount: float = 0
    preview: str = ""


def classify_owner_intent(text: str) -> OwnerIntent:
    lowered = re.sub(r"\s+", " ", (text or "").strip().lower())
    wants_skill = bool(re.search(r"\b(создай|добавь|измени).{0,20}навык\b", lowered))
    wants_preference = bool(
        re.search(r"\b(в следующий раз|в следующем аудите|в следующей сводке|всегда группируй|не используй эмодзи|больше не показывай)\b", lowered)
    )
    wants_commitment = bool(
        re.search(r"\b(я разберусь|проверю|напомни через|этим занимается мастер|пока не трогай|я уже написал)\b", lowered)
    )
    wants_action = bool(
        re.search(
            r"\b(дай|выдай|отправ|создай|перенес|измени|удали|сделай|выполни|запусти|компенсир|выстав)\w*",
            lowered,
        )
    )
    if wants_skill:
        group = "skills"
    elif wants_preference:
        group = "preferences"
    elif wants_commitment:
        group = "commitments"
    elif re.search(r"\b(отсроч|перенес.{0,20}оплат|новая дата)\b", lowered):
        group = "deferrals"
    elif re.search(r"\b(коммунал|показани|сч[её]тчик|тариф)\b", lowered):
        group = "utility"
    elif re.search(r"\b(расход|компенсац)\b", lowered):
        group = "expenses"
    elif re.search(r"\b(сообщ|напиш|рассыл)\b", lowered):
        group = "messages"
    elif re.search(r"\b(напомин)\b", lowered):
        group = "reminders"
    elif re.search(r"\b(отч[её]т|аналитик)\b", lowered):
        group = "reports"
    elif re.search(r"\b(объект|квартир|договор|жилец|арендатор)\b", lowered):
        group = "property_contract"
    elif re.search(r"\b(долг|оплат|плат[её]ж|просроч|аренд)\b", lowered):
        group = "read_only_finance" if not wants_action else "rent_payments"
    elif re.search(r"\b(настройк|режим|лимит|бюджет)\b", lowered):
        group = "system_settings"
    else:
        group = "read_only_finance"
    return OwnerIntent(
        group=group,
        read_only=not wants_action and not wants_skill,
        wants_detail=bool(re.search(r"\b(подроб|распиши|почему|история)\b", lowered)),
        wants_action=wants_action,
        wants_skill=wants_skill,
        wants_preference=wants_preference,
        wants_commitment=wants_commitment,
    )


def _conversation_summary(session: Session, chat_id: int | str) -> tuple[str, int]:
    conversation = session.scalar(
        select(AiConversation)
        .where(AiConversation.chat_id == str(chat_id), AiConversation.role == "owner", AiConversation.status == "active")
        .order_by(AiConversation.updated_at.desc(), AiConversation.id.desc())
        .limit(1)
    )
    if not conversation:
        return "", 0
    summary = session.scalar(
        select(ConversationSummary).where(ConversationSummary.conversation_id == conversation.id).limit(1)
    )
    messages = session.scalars(
        select(AiMessage)
        .where(AiMessage.conversation_id == conversation.id, AiMessage.role.in_({"user", "assistant"}))
        .order_by(AiMessage.created_at.desc(), AiMessage.id.desc())
        .limit(6)
    ).all()
    recent = [
        {"role": item.role, "text": re.sub(r"\s+", " ", item.text or "").strip()[:300]}
        for item in reversed(messages)
    ]
    value = {"rolling_summary": summary.summary if summary else "", "recent_messages": recent}
    return json.dumps(value, ensure_ascii=False, sort_keys=True), len(recent)


def build_owner_context(
    session: Session,
    *,
    chat_id: int | str,
    user_text: str,
    model: str,
    feature: str = "owner_chat",
    output_limit: int | None = None,
) -> SelectedContext:
    intent = classify_owner_intent(user_text)
    open_cases = reconcile_operational_cases(session)
    matched = resolve_case_alias(session, user_text)
    ambiguous: list[OperationalCase] = []
    if len(matched) > 1:
        ambiguous = matched
        selected: list[OperationalCase] = []
    elif matched:
        selected = matched[:1]
    else:
        limit = 6 if intent.group == "read_only_finance" else 3
        selected = open_cases[:limit]
    case_payload = [case_snapshot(session, item) for item in selected]
    scopes = ["owner_chat", *[f"property:{item.property_id}" for item in selected if item.property_id]]
    preferences = applicable_preferences(session, scopes=scopes)
    commitments = session.scalars(
        select(OwnerCommitment)
        .where(
            OwnerCommitment.status.in_({"active", "overdue"}),
            OwnerCommitment.case_id.in_([item.id for item in selected] or [-1]),
        )
        .order_by(OwnerCommitment.due_at, OwnerCommitment.id)
    ).all()
    summary, message_count = _conversation_summary(session, chat_id)
    tools = TOOL_GROUPS.get(intent.group, TOOL_GROUPS["read_only_finance"])
    payload = {
        "intent_group": intent.group,
        "cases": case_payload,
        "preferences": [
            {"scope": item.scope, "key": item.key, "value": preference_value(item), "mode": item.mode}
            for item in preferences
        ],
        "commitments": [
            {
                "id": item.id,
                "case_id": item.case_id,
                "description": item.description,
                "status": item.status,
                "due_at": item.due_at.isoformat() if item.due_at else None,
            }
            for item in commitments
        ],
        "conversation": json.loads(summary) if summary else {},
        "tools": tools,
    }
    context_text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    state_hash = stable_hash({"payload": payload, "user_text": user_text})
    manifest = ContextManifest(
        feature=feature,
        model=model,
        selected_case_ids=[item.id for item in selected],
        included_entities={
            "properties": sorted({item.property_id for item in selected if item.property_id}),
            "apartments": sorted({item.apartment_id for item in selected if item.apartment_id}),
            "contracts": sorted({item.contract_id for item in selected if item.contract_id}),
            "preferences": [item.id for item in preferences],
            "commitments": [item.id for item in commitments],
        },
        included_messages_count=message_count,
        approximate_input_size=max(1, len(context_text) // 4),
        reason_for_llm_usage="natural language owner request requires intent/extraction or response formulation",
        excluded_context=["full dashboard", "other case histories", "full owner chat", "unrelated tenant data", "database access"],
        output_limit=output_limit or FEATURE_OUTPUT_LIMITS.get(feature, 800),
        selected_tools=list(tools),
        state_hash=state_hash,
    )
    return SelectedContext(
        text=context_text,
        manifest=manifest,
        case_ids=[item.id for item in selected],
        ambiguous_case_ids=[item.id for item in ambiguous],
    )


def build_tenant_context(
    session: Session,
    *,
    lease: Lease,
    user_text: str,
    model: str,
    reason: str,
    output_limit: int | None = None,
) -> SelectedContext:
    charges = session.scalars(
        select(RentCharge)
        .where(RentCharge.lease_id == lease.id)
        .order_by(RentCharge.due_date.desc(), RentCharge.id.desc())
        .limit(4)
    ).all()
    utilities = session.scalars(
        select(UtilityBillLine)
        .where(UtilityBillLine.lease_id == lease.id)
        .order_by(UtilityBillLine.id.desc())
        .limit(4)
    ).all()
    debts = session.scalars(
        select(ManualDebt)
        .where(ManualDebt.lease_id == lease.id, ManualDebt.active.is_(True))
        .order_by(ManualDebt.id.desc())
        .limit(4)
    ).all()
    conversation = session.scalar(
        select(AiConversation)
        .where(AiConversation.lease_id == lease.id, AiConversation.role == "tenant", AiConversation.status == "active")
        .order_by(AiConversation.updated_at.desc(), AiConversation.id.desc())
        .limit(1)
    )
    messages: list[AiMessage] = []
    if conversation:
        messages = list(
            session.scalars(
                select(AiMessage)
                .where(AiMessage.conversation_id == conversation.id, AiMessage.role.in_({"user", "assistant"}))
                .order_by(AiMessage.created_at.desc(), AiMessage.id.desc())
                .limit(6)
            ).all()
        )
    payload = {
        "contract": {
            "id": lease.id,
            "property_id": lease.apartment.object_id,
            "apartment_id": lease.apartment_id,
            "tenant_id": lease.tenant_id,
            "payment_day": lease.payment_day,
            "rent_amount": round(float(lease.ip_amount or 0) + float(lease.personal_amount or 0), 2),
        },
        "rent_charges": [
            {
                "id": item.id,
                "due_date": item.due_date.isoformat(),
                "amount_due": round(float(item.ip_due or 0) + float(item.personal_due or 0), 2),
                "amount_paid": round(float(item.ip_paid or 0) + float(item.personal_paid or 0), 2),
                "status": item.status,
                "deferral_until": item.deferral_until.isoformat() if item.deferral_until else None,
            }
            for item in charges
        ],
        "utility_lines": [
            {
                "id": item.id,
                "due_date": item.due_date.isoformat() if item.due_date else None,
                "amount": item.total_amount,
                "paid": item.paid_amount,
                "status": item.status,
            }
            for item in utilities
        ],
        "manual_debts": [
            {"id": item.id, "title": item.title, "amount": item.amount, "paid": item.paid_amount, "status": item.status}
            for item in debts
        ],
        "recent_messages": [
            {"role": item.role, "text": re.sub(r"\s+", " ", item.text or "").strip()[:300]}
            for item in reversed(messages)
        ],
    }
    context_text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    state_hash = stable_hash({"payload": payload, "user_text": user_text})
    manifest = ContextManifest(
        feature="tenant_chat",
        model=model,
        selected_case_ids=[],
        included_entities={"contracts": [lease.id], "tenants": [lease.tenant_id], "apartments": [lease.apartment_id]},
        included_messages_count=len(messages),
        approximate_input_size=max(1, len(context_text) // 4),
        reason_for_llm_usage=reason,
        excluded_context=["other contracts", "other tenants", "owner dashboard", "full chat history", "database access"],
        output_limit=output_limit or FEATURE_OUTPUT_LIMITS["tenant_chat"],
        selected_tools=[],
        state_hash=state_hash,
    )
    return SelectedContext(text=context_text, manifest=manifest, case_ids=[])


def create_agent_run(
    session: Session,
    *,
    manifest: ContextManifest,
    trigger: str,
) -> HermesAgentRun:
    run = HermesAgentRun(
        run_id=str(uuid.uuid4()),
        feature=manifest.feature,
        trigger=trigger[:120],
        model=manifest.model or "no_llm",
        context_manifest_json=manifest.model_dump_json(),
        state_hash=manifest.state_hash,
        selected_tools_json=json.dumps(manifest.selected_tools, ensure_ascii=False),
        status="running",
    )
    session.add(run)
    session.flush()
    return run


def complete_agent_run(
    run: HermesAgentRun,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    estimated_cost: float = 0,
    envelope: dict[str, Any] | None = None,
    proposal_ids: list[int] | None = None,
    execution_result: dict[str, Any] | None = None,
    error: str = "",
    status: str = "completed",
) -> HermesAgentRun:
    run.input_tokens = int(input_tokens or 0)
    run.output_tokens = int(output_tokens or 0)
    run.estimated_cost = round(float(estimated_cost or 0), 4)
    run.normalized_envelope_json = json.dumps(envelope or {}, ensure_ascii=False, sort_keys=True)
    run.proposal_ids_json = json.dumps(proposal_ids or [])
    run.execution_result_json = json.dumps(execution_result or {}, ensure_ascii=False, sort_keys=True)
    run.error_text = error[:2000]
    run.status = "failed" if error else status
    run.completed_at = utc_now()
    return run


def record_feature_usage(
    session: Session,
    *,
    feature: str,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost_rub: float,
    usage_date: date | None = None,
) -> AiFeatureUsageDaily:
    usage_date = usage_date or date.today()
    usage = session.scalar(
        select(AiFeatureUsageDaily).where(
            AiFeatureUsageDaily.usage_date == usage_date,
            AiFeatureUsageDaily.feature == feature,
            AiFeatureUsageDaily.provider == provider,
            AiFeatureUsageDaily.model == model,
        )
    )
    if not usage:
        usage = AiFeatureUsageDaily(usage_date=usage_date, feature=feature, provider=provider, model=model)
        session.add(usage)
    usage.prompt_tokens = int(usage.prompt_tokens or 0) + int(prompt_tokens or 0)
    usage.completion_tokens = int(usage.completion_tokens or 0) + int(completion_tokens or 0)
    usage.total_tokens = int(usage.total_tokens or 0) + int(prompt_tokens or 0) + int(completion_tokens or 0)
    usage.cost_rub = round(float(usage.cost_rub or 0) + float(cost_rub or 0), 4)
    usage.calls = int(usage.calls or 0) + 1
    return usage


def unchanged_analysis_exists(session: Session, *, feature: str, state_hash: str, trigger: str) -> bool:
    if not state_hash or feature not in {"case_details", "deep_audit"}:
        return False
    return bool(
        session.scalar(
            select(HermesAgentRun.id)
            .where(
                HermesAgentRun.feature == feature,
                HermesAgentRun.state_hash == state_hash,
                HermesAgentRun.trigger == trigger[:120],
                HermesAgentRun.status == "completed",
            )
            .limit(1)
        )
    )


def _target_leases(session: Session, text: str) -> list[tuple[int, Lease]]:
    leases = session.scalars(
        select(Lease)
        .options(joinedload(Lease.apartment).joinedload(Apartment.object), joinedload(Lease.tenant))
        .where(Lease.active.is_(True))
        .order_by(Lease.id)
    ).unique().all()
    normalized = re.sub(r"[^0-9a-zа-яё]+", " ", text.lower()).strip()
    tokens = {token for token in normalized.split() if len(token) >= 1}
    scored: list[tuple[int, Lease]] = []
    for lease in leases:
        rental_object = lease.apartment.object
        aliases = " ".join(
            [
                rental_object.name,
                rental_object.short_code,
                lease.apartment.name,
                lease.tenant.full_name,
                str(lease.id),
            ]
        ).lower()
        alias_normalized = re.sub(r"[^0-9a-zа-яё]+", " ", aliases)
        score = sum(1 for token in tokens if token in alias_normalized)
        if score:
            scored.append((score, lease))
    return scored


def plan_grouped_deferral(
    session: Session,
    text: str,
    *,
    today: date | None = None,
) -> GroupedProposalPlan:
    today = today or date.today()
    lowered = re.sub(r"\s+", " ", (text or "").strip().lower())
    if not re.search(r"\b(дай|выдай|предостав|перенес).{0,30}(отсроч|оплат)", lowered):
        return GroupedProposalPlan()
    if not re.search(r"\b(все|всем|по всем)\b", lowered):
        return GroupedProposalPlan()
    days = 7 if re.search(r"недел", lowered) else 0
    match_days = re.search(r"(?:на|через)\s+(\d{1,3})\s*(?:дн|дня|день|дней)", lowered)
    if match_days:
        days = int(match_days.group(1))
    if days <= 0:
        days = 7
    new_date = today + timedelta(days=min(180, days))
    scored = _target_leases(session, lowered)
    if not scored:
        return GroupedProposalPlan(matched=True, new_date=new_date.isoformat())
    best_score = max(score for score, _lease in scored)
    best = [lease for score, lease in scored if score == best_score]
    property_ids = {lease.apartment.object_id for lease in best}
    if len(best) > 1 and len(property_ids) > 1:
        return GroupedProposalPlan(
            matched=True,
            ambiguous_lease_ids=[lease.id for lease in best],
            new_date=new_date.isoformat(),
        )
    target_leases = best
    lease_ids = [lease.id for lease in target_leases]
    items: list[dict[str, Any]] = []
    total = 0.0
    for charge in session.scalars(select(RentCharge).where(RentCharge.lease_id.in_(lease_ids))).all():
        debt = round(
            max(0, float(charge.ip_due or 0) - float(charge.ip_paid or 0))
            + max(0, float(charge.personal_due or 0) - float(charge.personal_paid or 0)),
            2,
        )
        if debt <= 0:
            continue
        items.append({"kind": "rent", "id": charge.id, "lease_id": charge.lease_id, "amount": debt})
        total += debt
    for line in session.scalars(select(UtilityBillLine).where(UtilityBillLine.lease_id.in_(lease_ids))).all():
        debt = round(max(0, float(line.total_amount or 0) - float(line.paid_amount or 0)), 2)
        if debt <= 0 or line.status in {"draft", "cancelled"}:
            continue
        items.append({"kind": "utility", "id": line.id, "lease_id": line.lease_id, "amount": debt})
        total += debt
    for manual_debt in session.scalars(
        select(ManualDebt).where(ManualDebt.lease_id.in_(lease_ids), ManualDebt.active.is_(True))
    ).all():
        remaining = round(max(0, float(manual_debt.amount or 0) - float(manual_debt.paid_amount or 0)), 2)
        if remaining <= 0:
            continue
        items.append(
            {
                "kind": "manual_debt",
                "id": manual_debt.id,
                "lease_id": manual_debt.lease_id,
                "amount": remaining,
            }
        )
        total += remaining
    target_label = target_leases[0].apartment.object.name if len(property_ids) == 1 else f"{len(target_leases)} договора"
    rendered_total = f"{total:,.2f}".replace(",", " ").replace(".00", "")
    preview = (
        f"Предлагаю дать отсрочку: {target_label}\n"
        f"Платежей: {len(items)}\n"
        f"Общая сумма: {rendered_total} ₽\n"
        f"Новая дата: {new_date:%d.%m.%Y}\n"
        "Последствие: автоматические финансовые напоминания будут на паузе до этой даты."
    )
    return GroupedProposalPlan(
        matched=True,
        lease_ids=lease_ids,
        items=items,
        new_date=new_date.isoformat(),
        total_amount=round(total, 2),
        preview=preview,
    )


def create_grouped_deferral_proposal(
    session: Session,
    *,
    conversation: AiConversation,
    owner_chat_id: int | str,
    plan: GroupedProposalPlan,
    ttl_hours: int = 48,
    mass_action_threshold: int = 20,
) -> AgentActionProposal:
    if not plan.matched or plan.ambiguous_lease_ids or not plan.items:
        raise ValueError("Для пакетной отсрочки не найден однозначный набор платежей")
    payload = {
        "items": plan.items,
        "new_date": plan.new_date,
        "lease_ids": plan.lease_ids,
        "target_count": len(plan.items),
        "total_amount": plan.total_amount,
    }
    idempotency_key = "grouped-deferral:" + stable_hash(payload)
    existing = session.scalar(
        select(AgentActionProposal).where(
            AgentActionProposal.idempotency_key == idempotency_key,
            AgentActionProposal.status == "pending",
        )
    )
    if existing:
        return existing
    decision = ActionSafetyRegistry(mass_action_threshold=mass_action_threshold).classify("grouped_deferral", payload)
    proposal = AgentActionProposal(
        conversation_id=conversation.id,
        action_type="grouped_deferral",
        status="pending",
        payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        preview_text=plan.preview,
        requested_by="hermes",
        owner_chat_id=str(owner_chat_id),
        expires_at=utc_now() + timedelta(hours=min(168, max(1, ttl_hours))),
        safety_level=decision.level,
        idempotency_key=idempotency_key,
        group_key=f"deferral:{stable_hash(sorted(plan.lease_ids))[:32]}",
        validation_hash=stable_hash(plan.items),
    )
    session.add(proposal)
    session.flush()
    return proposal


def execute_grouped_deferral(
    session: Session,
    proposal: AgentActionProposal,
    *,
    today: date | None = None,
) -> str:
    today = today or date.today()
    try:
        payload = json.loads(proposal.payload_json or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("Повреждены параметры пакетной отсрочки") from exc
    try:
        promised = date.fromisoformat(str(payload.get("new_date") or ""))
    except ValueError as exc:
        raise ValueError("Новая дата пакетной отсрочки повреждена") from exc
    if promised <= today or promised > today + timedelta(days=180):
        raise ValueError("Новая дата пакетной отсрочки больше не допустима")
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("В пакетной отсрочке нет платежей")
    resolved: list[tuple[str, Any, float]] = []
    for raw in items:
        if not isinstance(raw, dict):
            raise ValueError("Повреждён элемент пакетной отсрочки")
        kind = str(raw.get("kind") or "")
        entity_id = int(raw.get("id") or 0)
        entity: Any
        if kind == "rent":
            entity = session.get(RentCharge, entity_id)
            if not entity:
                raise ValueError(f"Начисление аренды #{entity_id} не найдено")
            debt = max(0, float(entity.ip_due or 0) - float(entity.ip_paid or 0)) + max(
                0, float(entity.personal_due or 0) - float(entity.personal_paid or 0)
            )
        elif kind == "utility":
            entity = session.get(UtilityBillLine, entity_id)
            if not entity:
                raise ValueError(f"Коммунальный платёж #{entity_id} не найден")
            debt = max(0, float(entity.total_amount or 0) - float(entity.paid_amount or 0))
        elif kind == "manual_debt":
            entity = session.get(ManualDebt, entity_id)
            if not entity or not entity.active:
                raise ValueError(f"Ручной долг #{entity_id} не найден")
            debt = max(0, float(entity.amount or 0) - float(entity.paid_amount or 0))
        else:
            raise ValueError(f"Тип платежа {kind} не поддерживается")
        if debt <= 0:
            raise ValueError(f"Платёж {kind} #{entity_id} уже закрыт")
        resolved.append((kind, entity, debt))
    with session.begin_nested():
        for kind, entity, _debt in resolved:
            if kind == "rent":
                entity.deferral_until = promised
                entity.deferral_note = f"Пакетная отсрочка Hermes proposal #{proposal.id}"
            elif kind == "manual_debt":
                entity.due_date = promised
                entity.updated_at = utc_now()
            else:
                situation = session.scalar(
                    select(PaymentSituation).where(
                        PaymentSituation.kind == "utility",
                        PaymentSituation.reference_id == entity.id,
                    )
                )
                if not situation:
                    situation = PaymentSituation(
                        lease_id=int(entity.lease_id),
                        kind="utility",
                        reference_id=entity.id,
                    )
                    session.add(situation)
                situation.promise_date = promised
                situation.paused_until = promised
                situation.status = "promised"
            if kind == "rent":
                situation = session.scalar(
                    select(PaymentSituation).where(
                        PaymentSituation.kind == "rent",
                        PaymentSituation.reference_id == entity.id,
                    )
                )
                if not situation:
                    situation = PaymentSituation(lease_id=entity.lease_id, kind="rent", reference_id=entity.id)
                    session.add(situation)
                situation.promise_date = promised
                situation.paused_until = promised
                situation.status = "promised"
        session.flush()
    reconcile_operational_cases(session, today)
    return f"Отсрочка до {promised:%d.%m.%Y} применена к {len(resolved)} платежам одной транзакцией."


def usage_summary(session: Session, *, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    month_start = today.replace(day=1)
    rows = session.scalars(
        select(AiFeatureUsageDaily)
        .where(AiFeatureUsageDaily.usage_date >= month_start)
        .order_by(AiFeatureUsageDaily.usage_date, AiFeatureUsageDaily.feature)
    ).all()
    features: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = features.setdefault(
            row.feature,
            {
                "calls": 0,
                "tokens": 0,
                "cost_rub": 0.0,
                "today_calls": 0,
                "today_tokens": 0,
                "today_cost_rub": 0.0,
                "models": set(),
            },
        )
        item["calls"] += int(row.calls or 0)
        item["tokens"] += int(row.total_tokens or 0)
        item["cost_rub"] = round(item["cost_rub"] + float(row.cost_rub or 0), 2)
        if row.usage_date == today:
            item["today_calls"] += int(row.calls or 0)
            item["today_tokens"] += int(row.total_tokens or 0)
            item["today_cost_rub"] = round(item["today_cost_rub"] + float(row.cost_rub or 0), 2)
        item["models"].add(row.model)
    for value in features.values():
        value["models"] = sorted(value["models"])
    spent = round(sum(value["cost_rub"] for value in features.values()), 2)
    elapsed = max(1, today.day)
    days_in_month = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    forecast = round(spent / elapsed * days_in_month.day, 2)
    return {"month_spent_rub": spent, "month_forecast_rub": forecast, "features": features}
