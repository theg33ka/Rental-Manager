from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from rental_manager.database import Base


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class RentalObject(Base):
    __tablename__ = "rental_objects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    short_code: Mapped[str] = mapped_column(String(20), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    apartments: Mapped[list["Apartment"]] = relationship(back_populates="object", cascade="all, delete-orphan")
    services: Mapped[list["UtilityService"]] = relationship(back_populates="object", cascade="all, delete-orphan")


class Apartment(Base):
    __tablename__ = "apartments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    object_id: Mapped[int] = mapped_column(ForeignKey("rental_objects.id"))
    name: Mapped[str] = mapped_column(String(80))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    odn_share_percent: Mapped[float] = mapped_column(Float, default=25.0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    object: Mapped[RentalObject] = relationship(back_populates="apartments")
    leases: Mapped[list["Lease"]] = relationship(back_populates="apartment")
    meters: Mapped[list["Meter"]] = relationship(back_populates="apartment")
    utility_advance_setting: Mapped["UtilityAdvanceSetting | None"] = relationship(back_populates="apartment")


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(180))
    phone: Mapped[str] = mapped_column(String(60), default="")
    telegram: Mapped[str] = mapped_column(String(80), default="")
    whatsapp: Mapped[str] = mapped_column(String(80), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    leases: Mapped[list["Lease"]] = relationship(back_populates="tenant")


class Lease(Base):
    __tablename__ = "leases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    apartment_id: Mapped[int] = mapped_column(ForeignKey("apartments.id"))
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    payment_day: Mapped[int] = mapped_column(Integer)
    ip_amount: Mapped[float] = mapped_column(Float, default=0)
    personal_amount: Mapped[float] = mapped_column(Float, default=0)
    deposit_amount: Mapped[float] = mapped_column(Float, default=0)
    deposit_location: Mapped[str] = mapped_column(String(180), default="")
    deposit_terms: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    apartment: Mapped[Apartment] = relationship(back_populates="leases")
    tenant: Mapped[Tenant] = relationship(back_populates="leases")
    rent_charges: Mapped[list["RentCharge"]] = relationship(back_populates="lease")
    manual_debts: Mapped[list["ManualDebt"]] = relationship(back_populates="lease")


class RentCharge(Base):
    __tablename__ = "rent_charges"
    __table_args__ = (UniqueConstraint("lease_id", "due_date", name="uq_rent_charge_lease_due"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lease_id: Mapped[int] = mapped_column(ForeignKey("leases.id"))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)
    ip_due: Mapped[float] = mapped_column(Float, default=0)
    personal_due: Mapped[float] = mapped_column(Float, default=0)
    ip_paid: Mapped[float] = mapped_column(Float, default=0)
    personal_paid: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(40), default="pending")
    deferral_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    deferral_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    lease: Mapped[Lease] = relationship(back_populates="rent_charges")
    receipts: Mapped[list["PaymentReceipt"]] = relationship(back_populates="rent_charge")


class PaymentReceipt(Base):
    __tablename__ = "payment_receipts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lease_id: Mapped[int | None] = mapped_column(ForeignKey("leases.id"), nullable=True)
    rent_charge_id: Mapped[int | None] = mapped_column(ForeignKey("rent_charges.id"), nullable=True)
    utility_line_id: Mapped[int | None] = mapped_column(ForeignKey("utility_bill_lines.id"), nullable=True)
    apartment_id: Mapped[int | None] = mapped_column(ForeignKey("apartments.id"), nullable=True)
    amount: Mapped[float] = mapped_column(Float)
    channel: Mapped[str] = mapped_column(String(40))
    paid_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    source: Mapped[str] = mapped_column(String(40), default="manual")
    status: Mapped[str] = mapped_column(String(40), default="accepted")
    recipient_name: Mapped[str] = mapped_column(String(180), default="")
    recipient_details: Mapped[str] = mapped_column(Text, default="")
    file_path: Mapped[str] = mapped_column(String(500), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    rent_charge: Mapped[RentCharge | None] = relationship(back_populates="receipts")


class MessageLog(Base):
    __tablename__ = "message_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lease_id: Mapped[int | None] = mapped_column(ForeignKey("leases.id"), nullable=True)
    rent_charge_id: Mapped[int | None] = mapped_column(ForeignKey("rent_charges.id"), nullable=True)
    utility_line_id: Mapped[int | None] = mapped_column(ForeignKey("utility_bill_lines.id"), nullable=True)
    channel: Mapped[str] = mapped_column(String(40), default="telegram")
    template_key: Mapped[str] = mapped_column(String(80), default="")
    status: Mapped[str] = mapped_column(String(40), default="sent")
    recipient_chat_id: Mapped[str] = mapped_column(String(80), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class AiConversation(Base):
    __tablename__ = "ai_conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[str] = mapped_column(String(80), default="")
    role: Mapped[str] = mapped_column(String(40), default="tenant")
    lease_id: Mapped[int | None] = mapped_column(ForeignKey("leases.id"), nullable=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="active")
    title: Mapped[str] = mapped_column(String(180), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    lease: Mapped[Lease | None] = relationship()
    tenant: Mapped[Tenant | None] = relationship()
    messages: Mapped[list["AiMessage"]] = relationship(back_populates="conversation", cascade="all, delete-orphan")


class AiMessage(Base):
    __tablename__ = "ai_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("ai_conversations.id"), nullable=True)
    lease_id: Mapped[int | None] = mapped_column(ForeignKey("leases.id"), nullable=True)
    role: Mapped[str] = mapped_column(String(40), default="user")
    channel: Mapped[str] = mapped_column(String(40), default="telegram")
    text: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_rub: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    conversation: Mapped[AiConversation | None] = relationship(back_populates="messages")
    lease: Mapped[Lease | None] = relationship()


class AiActionLog(Base):
    __tablename__ = "ai_action_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("ai_conversations.id"), nullable=True)
    lease_id: Mapped[int | None] = mapped_column(ForeignKey("leases.id"), nullable=True)
    actor_role: Mapped[str] = mapped_column(String(40), default="")
    action_type: Mapped[str] = mapped_column(String(80), default="")
    status: Mapped[str] = mapped_column(String(40), default="")
    payload_json: Mapped[str] = mapped_column(Text, default="")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    conversation: Mapped[AiConversation | None] = relationship()
    lease: Mapped[Lease | None] = relationship()


class AgentMemory(Base):
    __tablename__ = "agent_memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope_type: Mapped[str] = mapped_column(String(40), default="owner")
    scope_id: Mapped[str] = mapped_column(String(80), default="")
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("ai_conversations.id"), nullable=True)
    lease_id: Mapped[int | None] = mapped_column(ForeignKey("leases.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(40), default="fact")
    content: Mapped[str] = mapped_column(Text, default="")
    importance: Mapped[int] = mapped_column(Integer, default=1)
    source: Mapped[str] = mapped_column(String(40), default="agent")
    status: Mapped[str] = mapped_column(String(40), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    conversation: Mapped[AiConversation | None] = relationship()
    lease: Mapped[Lease | None] = relationship()


class AgentActionProposal(Base):
    __tablename__ = "agent_action_proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("ai_conversations.id"), nullable=True)
    lease_id: Mapped[int | None] = mapped_column(ForeignKey("leases.id"), nullable=True)
    action_type: Mapped[str] = mapped_column(String(80), default="")
    status: Mapped[str] = mapped_column(String(40), default="pending")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    preview_text: Mapped[str] = mapped_column(Text, default="")
    result_text: Mapped[str] = mapped_column(Text, default="")
    error_text: Mapped[str] = mapped_column(Text, default="")
    requested_by: Mapped[str] = mapped_column(String(40), default="agent")
    owner_chat_id: Mapped[str] = mapped_column(String(80), default="")
    owner_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("operational_cases.id"), nullable=True)
    safety_level: Mapped[int] = mapped_column(Integer, default=2)
    idempotency_key: Mapped[str] = mapped_column(String(160), default="")
    group_key: Mapped[str] = mapped_column(String(160), default="")
    validation_hash: Mapped[str] = mapped_column(String(128), default="")
    confirmed_by: Mapped[str] = mapped_column(String(80), default="")

    conversation: Mapped[AiConversation | None] = relationship()
    lease: Mapped[Lease | None] = relationship()


class AgentTenantState(Base):
    __tablename__ = "agent_tenant_states"
    __table_args__ = (UniqueConstraint("lease_id", name="uq_agent_tenant_state_lease"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lease_id: Mapped[int] = mapped_column(ForeignKey("leases.id"))
    status: Mapped[str] = mapped_column(String(40), default="normal")
    escalation_level: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_excuses: Mapped[int] = mapped_column(Integer, default=0)
    promise_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    promise_text: Mapped[str] = mapped_column(Text, default="")
    next_contact_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_tenant_message_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_agent_message_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_owner_update_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    lease: Mapped[Lease] = relationship()


class PaymentSituation(Base):
    __tablename__ = "payment_situations"
    __table_args__ = (UniqueConstraint("kind", "reference_id", name="uq_payment_situation_kind_reference"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lease_id: Mapped[int] = mapped_column(ForeignKey("leases.id"))
    kind: Mapped[str] = mapped_column(String(40))
    reference_id: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(60), default="awaiting_payment")
    mode: Mapped[str] = mapped_column(String(20), default="normal")
    notification_count: Mapped[int] = mapped_column(Integer, default=0)
    tenant_response_count: Mapped[int] = mapped_column(Integer, default=0)
    promise_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    paused_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_notification_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_tenant_response_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    owner_last_notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    lease: Mapped[Lease] = relationship()


class AgentTask(Base):
    __tablename__ = "agent_tasks"
    __table_args__ = (UniqueConstraint("issue_key", name="uq_agent_task_issue_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    issue_key: Mapped[str] = mapped_column(String(160))
    lease_id: Mapped[int | None] = mapped_column(ForeignKey("leases.id"), nullable=True)
    category: Mapped[str] = mapped_column(String(60), default="general")
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    status: Mapped[str] = mapped_column(String(40), default="open")
    title: Mapped[str] = mapped_column(String(240), default="")
    details: Mapped[str] = mapped_column(Text, default="")
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notification_count: Mapped[int] = mapped_column(Integer, default=0)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    lease: Mapped[Lease | None] = relationship()


class DomainEvent(Base):
    __tablename__ = "domain_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    entity_type: Mapped[str] = mapped_column(String(80), default="")
    entity_id: Mapped[str] = mapped_column(String(80), default="")
    property_id: Mapped[int | None] = mapped_column(ForeignKey("rental_objects.id"), nullable=True)
    apartment_id: Mapped[int | None] = mapped_column(ForeignKey("apartments.id"), nullable=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True)
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("leases.id"), nullable=True)
    actor_type: Mapped[str] = mapped_column(String(40), default="system")
    source: Mapped[str] = mapped_column(String(80), default="backend")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    correlation_id: Mapped[str] = mapped_column(String(120), default="", index=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class OperationalCase(Base):
    __tablename__ = "operational_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_key: Mapped[str] = mapped_column(String(200), unique=True)
    case_type: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(40), default="new", index=True)
    severity: Mapped[str] = mapped_column(String(20), default="medium", index=True)
    priority_score: Mapped[float] = mapped_column(Float, default=0)
    property_id: Mapped[int | None] = mapped_column(ForeignKey("rental_objects.id"), nullable=True)
    apartment_id: Mapped[int | None] = mapped_column(ForeignKey("apartments.id"), nullable=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True)
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("leases.id"), nullable=True)
    parent_case_id: Mapped[int | None] = mapped_column(ForeignKey("operational_cases.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(240), default="")
    compact_summary: Mapped[str] = mapped_column(Text, default="")
    amount_total: Mapped[float] = mapped_column(Float, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="RUB")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    last_changed_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    next_review_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    assigned_actor: Mapped[str] = mapped_column(String(40), default="hermes")
    waiting_for: Mapped[str] = mapped_column(String(40), default="")
    suppression_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolution_reason: Mapped[str] = mapped_column(Text, default="")
    state_hash: Mapped[str] = mapped_column(String(128), default="")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class OwnerCommitment(Base):
    __tablename__ = "owner_commitments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("operational_cases.id"), nullable=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default="active", index=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_from_message_id: Mapped[int | None] = mapped_column(ForeignKey("ai_messages.id"), nullable=True)
    completion_condition: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class OwnerPreference(Base):
    __tablename__ = "owner_preferences"
    __table_args__ = (UniqueConstraint("scope", "key", "mode", "enabled", name="uq_owner_preference_active_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope: Mapped[str] = mapped_column(String(120), default="global", index=True)
    key: Mapped[str] = mapped_column(String(120), index=True)
    value_json: Mapped[str] = mapped_column(Text, default="{}")
    source: Mapped[str] = mapped_column(String(80), default="owner_explicit")
    mode: Mapped[str] = mapped_column(String(20), default="persistent")
    valid_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    legacy_memory_id: Mapped[int | None] = mapped_column(ForeignKey("agent_memories.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class TenantStrategyProfile(Base):
    __tablename__ = "tenant_strategy_profiles"
    __table_args__ = (UniqueConstraint("contract_id", name="uq_tenant_strategy_contract"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    contract_id: Mapped[int] = mapped_column(ForeignKey("leases.id"), index=True)
    preferred_channel: Mapped[str] = mapped_column(String(40), default="telegram")
    preferred_contact_window: Mapped[str] = mapped_column(String(40), default="10:00-20:00")
    response_speed_bucket: Mapped[str] = mapped_column(String(40), default="unknown")
    reminder_stage: Mapped[str] = mapped_column(String(40), default="pre_due")
    soft_reminder_effectiveness: Mapped[float] = mapped_column(Float, default=0)
    broken_promises_count: Mapped[int] = mapped_column(Integer, default=0)
    active_payment_situation: Mapped[str] = mapped_column(String(80), default="")
    escalation_threshold: Mapped[int] = mapped_column(Integer, default=3)
    owner_only_topics_json: Mapped[str] = mapped_column(Text, default="[]")
    last_strategy_change: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    strategy_source: Mapped[str] = mapped_column(String(80), default="observed_contract_history")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class CaseMemory(Base):
    __tablename__ = "case_memories"
    __table_args__ = (UniqueConstraint("case_id", name="uq_case_memory_case"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("operational_cases.id"), index=True)
    rolling_summary: Mapped[str] = mapped_column(Text, default="")
    state_hash: Mapped[str] = mapped_column(String(128), default="")
    last_message_id: Mapped[int | None] = mapped_column(ForeignKey("ai_messages.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class AiSkill(Base):
    __tablename__ = "ai_skills"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_ai_skill_name_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    goal: Mapped[str] = mapped_column(Text, default="")
    trigger_type: Mapped[str] = mapped_column(String(80), default="manual")
    trigger_config_json: Mapped[str] = mapped_column(Text, default="{}")
    preconditions_json: Mapped[str] = mapped_column(Text, default="[]")
    steps_json: Mapped[str] = mapped_column(Text, default="[]")
    allowed_tools_json: Mapped[str] = mapped_column(Text, default="[]")
    autonomous_tools_json: Mapped[str] = mapped_column(Text, default="[]")
    confirmation_required_tools_json: Mapped[str] = mapped_column(Text, default="[]")
    success_metric_json: Mapped[str] = mapped_column(Text, default="{}")
    scope: Mapped[str] = mapped_column(String(120), default="global")
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(40), default="draft", index=True)
    created_by: Mapped[str] = mapped_column(String(80), default="owner")
    previous_version_id: Mapped[int | None] = mapped_column(ForeignKey("ai_skills.id"), nullable=True)
    legacy_memory_id: Mapped[int | None] = mapped_column(ForeignKey("agent_memories.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class AiSkillRun(Base):
    __tablename__ = "ai_skill_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    skill_id: Mapped[int] = mapped_column(ForeignKey("ai_skills.id"), index=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("operational_cases.id"), nullable=True)
    trigger_event_id: Mapped[int | None] = mapped_column(ForeignKey("domain_events.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="started")
    selected_tools_json: Mapped[str] = mapped_column(Text, default="[]")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    error_text: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OwnerBriefing(Base):
    __tablename__ = "owner_briefings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    briefing_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(40), default="draft")
    text: Mapped[str] = mapped_column(Text, default="")
    state_hash: Mapped[str] = mapped_column(String(128), default="")
    preference_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    telegram_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class OwnerBriefingItem(Base):
    __tablename__ = "owner_briefing_items"
    __table_args__ = (UniqueConstraint("briefing_id", "case_id", name="uq_briefing_case"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    briefing_id: Mapped[int] = mapped_column(ForeignKey("owner_briefings.id"), index=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("operational_cases.id"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    selection_reason: Mapped[str] = mapped_column(Text, default="")
    state_hash: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class ReminderOutcome(Base):
    __tablename__ = "reminder_outcomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contract_id: Mapped[int] = mapped_column(ForeignKey("leases.id"), index=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("operational_cases.id"), nullable=True)
    payment_situation_id: Mapped[int | None] = mapped_column(ForeignKey("payment_situations.id"), nullable=True)
    message_log_id: Mapped[int | None] = mapped_column(ForeignKey("message_logs.id"), nullable=True)
    stage: Mapped[str] = mapped_column(String(40), default="")
    template_key: Mapped[str] = mapped_column(String(80), default="")
    outcome: Mapped[str] = mapped_column(String(40), default="delivered", index=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    outcome_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")


class HermesAgentRun(Base):
    __tablename__ = "hermes_agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(120), unique=True)
    feature: Mapped[str] = mapped_column(String(80), index=True)
    trigger: Mapped[str] = mapped_column(String(120), default="")
    model: Mapped[str] = mapped_column(String(120), default="no_llm")
    context_manifest_json: Mapped[str] = mapped_column(Text, default="{}")
    state_hash: Mapped[str] = mapped_column(String(128), default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0)
    selected_tools_json: Mapped[str] = mapped_column(Text, default="[]")
    proposal_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    execution_result_json: Mapped[str] = mapped_column(Text, default="{}")
    normalized_envelope_json: Mapped[str] = mapped_column(Text, default="{}")
    error_text: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default="running", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ConversationSummary(Base):
    __tablename__ = "conversation_summaries"
    __table_args__ = (UniqueConstraint("conversation_id", name="uq_conversation_summary"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("ai_conversations.id"), index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    last_message_id: Mapped[int | None] = mapped_column(ForeignKey("ai_messages.id"), nullable=True)
    state_hash: Mapped[str] = mapped_column(String(128), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class AiFeatureUsageDaily(Base):
    __tablename__ = "ai_feature_usage_daily"
    __table_args__ = (
        UniqueConstraint("usage_date", "feature", "provider", "model", name="uq_ai_feature_usage_daily"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usage_date: Mapped[date] = mapped_column(Date)
    feature: Mapped[str] = mapped_column(String(80), index=True)
    provider: Mapped[str] = mapped_column(String(40), default="deepseek")
    model: Mapped[str] = mapped_column(String(120), default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_rub: Mapped[float] = mapped_column(Float, default=0)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class AiUsageDaily(Base):
    __tablename__ = "ai_usage_daily"
    __table_args__ = (UniqueConstraint("usage_date", "provider", "model", name="uq_ai_usage_daily_provider_model"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usage_date: Mapped[date] = mapped_column(Date)
    provider: Mapped[str] = mapped_column(String(40), default="deepseek")
    model: Mapped[str] = mapped_column(String(120), default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_rub: Mapped[float] = mapped_column(Float, default=0)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class UtilityService(Base):
    __tablename__ = "utility_services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    object_id: Mapped[int] = mapped_column(ForeignKey("rental_objects.id"))
    kind: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(120))
    provider_reading_due_day: Mapped[int] = mapped_column(Integer, default=20)
    provider_due_day: Mapped[int] = mapped_column(Integer, default=24)
    resident_due_days: Mapped[int] = mapped_column(Integer, default=7)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    object: Mapped[RentalObject] = relationship(back_populates="services")
    meters: Mapped[list["Meter"]] = relationship(back_populates="service", cascade="all, delete-orphan")
    tariffs: Mapped[list["Tariff"]] = relationship(back_populates="service", cascade="all, delete-orphan")
    bills: Mapped[list["UtilityBill"]] = relationship(back_populates="service")


class Meter(Base):
    __tablename__ = "meters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("utility_services.id"))
    object_id: Mapped[int] = mapped_column(ForeignKey("rental_objects.id"))
    apartment_id: Mapped[int | None] = mapped_column(ForeignKey("apartments.id"), nullable=True)
    scope: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    service: Mapped[UtilityService] = relationship(back_populates="meters")
    apartment: Mapped[Apartment | None] = relationship(back_populates="meters")
    readings: Mapped[list["MeterReading"]] = relationship(back_populates="meter", cascade="all, delete-orphan")


class MeterReading(Base):
    __tablename__ = "meter_readings"
    __table_args__ = (UniqueConstraint("meter_id", "reading_date", name="uq_meter_reading_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    meter_id: Mapped[int] = mapped_column(ForeignKey("meters.id"))
    reading_date: Mapped[date] = mapped_column(Date)
    value: Mapped[float] = mapped_column(Float)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    meter: Mapped[Meter] = relationship(back_populates="readings")


class Tariff(Base):
    __tablename__ = "tariffs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("utility_services.id"))
    starts_on: Mapped[date] = mapped_column(Date)
    name: Mapped[str] = mapped_column(String(120), default="")
    tiers_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    service: Mapped[UtilityService] = relationship(back_populates="tariffs")


class UtilityBill(Base):
    __tablename__ = "utility_bills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("utility_services.id"))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    bill_type: Mapped[str] = mapped_column(String(40), default="utility")
    status: Mapped[str] = mapped_column(String(40), default="draft")
    total_consumption: Mapped[float] = mapped_column(Float, default=0)
    apartment_consumption: Mapped[float] = mapped_column(Float, default=0)
    odn_consumption: Mapped[float] = mapped_column(Float, default=0)
    total_cost: Mapped[float] = mapped_column(Float, default=0)
    average_unit_price: Mapped[float] = mapped_column(Float, default=0)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_forecast: Mapped[bool] = mapped_column(Boolean, default=False)
    provider_paid: Mapped[bool] = mapped_column(Boolean, default=False)
    provider_paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    service: Mapped[UtilityService] = relationship(back_populates="bills")
    lines: Mapped[list["UtilityBillLine"]] = relationship(back_populates="bill", cascade="all, delete-orphan")


class UtilityBillLine(Base):
    __tablename__ = "utility_bill_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bill_id: Mapped[int] = mapped_column(ForeignKey("utility_bills.id"))
    apartment_id: Mapped[int] = mapped_column(ForeignKey("apartments.id"))
    lease_id: Mapped[int | None] = mapped_column(ForeignKey("leases.id"), nullable=True)
    line_type: Mapped[str] = mapped_column(String(40), default="usage")
    personal_consumption: Mapped[float] = mapped_column(Float, default=0)
    odn_consumption: Mapped[float] = mapped_column(Float, default=0)
    total_amount: Mapped[float] = mapped_column(Float, default=0)
    paid_amount: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(40), default="draft")
    issued_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")

    bill: Mapped[UtilityBill] = relationship(back_populates="lines")
    apartment: Mapped[Apartment] = relationship()
    lease: Mapped[Lease | None] = relationship()


class UtilityAdvanceSetting(Base):
    __tablename__ = "utility_advance_settings"
    __table_args__ = (UniqueConstraint("apartment_id", name="uq_utility_advance_setting_apartment"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    apartment_id: Mapped[int] = mapped_column(ForeignKey("apartments.id"))
    amount_override: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    apartment: Mapped[Apartment] = relationship(back_populates="utility_advance_setting")


class UtilityAdvanceLedger(Base):
    __tablename__ = "utility_advance_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    apartment_id: Mapped[int] = mapped_column(ForeignKey("apartments.id"))
    lease_id: Mapped[int | None] = mapped_column(ForeignKey("leases.id"), nullable=True)
    utility_line_id: Mapped[int | None] = mapped_column(ForeignKey("utility_bill_lines.id"), nullable=True)
    payment_receipt_id: Mapped[int | None] = mapped_column(ForeignKey("payment_receipts.id"), nullable=True)
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    amount: Mapped[float] = mapped_column(Float, default=0)
    kind: Mapped[str] = mapped_column(String(40), default="adjustment")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    apartment: Mapped[Apartment] = relationship()
    lease: Mapped[Lease | None] = relationship()
    utility_line: Mapped[UtilityBillLine | None] = relationship()
    payment_receipt: Mapped[PaymentReceipt | None] = relationship()


class UtilityAdvanceSettingHistory(Base):
    __tablename__ = "utility_advance_setting_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    apartment_id: Mapped[int] = mapped_column(ForeignKey("apartments.id"))
    old_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    new_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    actor: Mapped[str] = mapped_column(String(40), default="owner")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    apartment: Mapped[Apartment] = relationship()


class ManualDebt(Base):
    __tablename__ = "manual_debts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lease_id: Mapped[int] = mapped_column(ForeignKey("leases.id"))
    apartment_id: Mapped[int] = mapped_column(ForeignKey("apartments.id"))
    kind: Mapped[str] = mapped_column(String(40), default="other")
    channel: Mapped[str] = mapped_column(String(40), default="")
    title: Mapped[str] = mapped_column(String(180), default="")
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    amount: Mapped[float] = mapped_column(Float, default=0)
    paid_amount: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(40), default="open")
    notes: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    lease: Mapped[Lease] = relationship(back_populates="manual_debts")
    apartment: Mapped[Apartment] = relationship()


class Expense(Base):
    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    expense_date: Mapped[date] = mapped_column(Date)
    object_id: Mapped[int | None] = mapped_column(ForeignKey("rental_objects.id"), nullable=True)
    apartment_id: Mapped[int | None] = mapped_column(ForeignKey("apartments.id"), nullable=True)
    category: Mapped[str] = mapped_column(String(100), default="")
    amount: Mapped[float] = mapped_column(Float)
    source_funds: Mapped[str] = mapped_column(String(60), default="personal")
    payment_method: Mapped[str] = mapped_column(String(80), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    compensation_status: Mapped[str] = mapped_column(String(60), default="pending")
    compensated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    file_path: Mapped[str] = mapped_column(String(500), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    object: Mapped[RentalObject | None] = relationship()
    apartment: Mapped[Apartment | None] = relationship()


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class PanelSession(Base):
    __tablename__ = "panel_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    csrf_token_hash: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    user_agent: Mapped[str] = mapped_column(String(240), default="")


class PanelLoginAttempt(Base):
    __tablename__ = "panel_login_attempts"

    fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    last_failed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)


class ProcessedTelegramUpdate(Base):
    __tablename__ = "processed_telegram_updates"

    update_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
