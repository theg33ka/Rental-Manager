"""Add the Hermes Core operational agent schema.

Revision ID: 20260715_01
Revises: 20260711_02
Create Date: 2026-07-15
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
import hashlib
import json

from alembic import op
import sqlalchemy as sa

from rental_manager.database import Base
from rental_manager import models  # noqa: F401


revision: str = "20260715_01"
down_revision: str | None = "20260711_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


NEW_TABLES = (
    "domain_events",
    "operational_cases",
    "owner_commitments",
    "owner_preferences",
    "tenant_strategy_profiles",
    "case_memories",
    "ai_skills",
    "ai_skill_runs",
    "owner_briefings",
    "owner_briefing_items",
    "reminder_outcomes",
    "hermes_agent_runs",
    "conversation_summaries",
    "ai_feature_usage_daily",
)


PROPOSAL_COLUMNS = (
    sa.Column("case_id", sa.Integer(), sa.ForeignKey("operational_cases.id"), nullable=True),
    sa.Column("safety_level", sa.Integer(), nullable=False, server_default="2"),
    sa.Column("idempotency_key", sa.String(160), nullable=False, server_default=""),
    sa.Column("group_key", sa.String(160), nullable=False, server_default=""),
    sa.Column("validation_hash", sa.String(128), nullable=False, server_default=""),
    sa.Column("confirmed_by", sa.String(80), nullable=False, server_default=""),
)


def _create_new_tables(bind) -> None:
    for table_name in NEW_TABLES:
        Base.metadata.tables[table_name].create(bind=bind, checkfirst=True)


def _add_proposal_columns(bind) -> None:
    inspector = sa.inspect(bind)
    existing = {column["name"] for column in inspector.get_columns("agent_action_proposals")}
    for column in PROPOSAL_COLUMNS:
        if column.name not in existing:
            op.add_column("agent_action_proposals", column)


def _migrate_agent_tasks(bind) -> None:
    inspector = sa.inspect(bind)
    if "agent_tasks" not in inspector.get_table_names():
        return
    tasks = sa.table(
        "agent_tasks",
        sa.column("id", sa.Integer()),
        sa.column("issue_key", sa.String()),
        sa.column("lease_id", sa.Integer()),
        sa.column("category", sa.String()),
        sa.column("severity", sa.String()),
        sa.column("status", sa.String()),
        sa.column("title", sa.String()),
        sa.column("details", sa.Text()),
        sa.column("due_date", sa.Date()),
        sa.column("first_seen_at", sa.DateTime()),
        sa.column("last_seen_at", sa.DateTime()),
        sa.column("resolved_at", sa.DateTime()),
    )
    cases = Base.metadata.tables["operational_cases"]
    existing_keys = set(bind.execute(sa.select(cases.c.case_key)).scalars())
    category_map = {
        "rent": "rent_overdue",
        "utility": "utility_overdue",
        "receipt": "suspicious_payment",
        "reading": "stale_meter_reading",
        "manual_debt": "manual_debt",
        "expense": "expense_compensation",
        "provider": "utility_overdue",
        "report": "custom_operational_issue",
    }
    now = datetime.utcnow()
    for row in bind.execute(sa.select(tasks)).mappings():
        case_key = f"legacy-task:{row['issue_key']}"
        if case_key in existing_keys:
            continue
        payload = {
            "legacy_table": "agent_tasks",
            "legacy_id": row["id"],
            "legacy_issue_key": row["issue_key"],
        }
        state_hash = hashlib.sha256(
            json.dumps([row["status"], row["severity"], row["details"]], ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        bind.execute(
            cases.insert().values(
                case_key=case_key,
                case_type=category_map.get(row["category"], "custom_operational_issue"),
                status="resolved" if row["status"] == "resolved" else "active",
                severity=row["severity"] or "medium",
                priority_score=0,
                contract_id=row["lease_id"],
                title=row["title"] or "Legacy operational task",
                compact_summary=row["details"] or "",
                amount_total=0,
                currency="RUB",
                first_seen_at=row["first_seen_at"] or now,
                last_changed_at=row["last_seen_at"] or now,
                next_review_at=None,
                assigned_actor="hermes",
                waiting_for="",
                resolved_at=row["resolved_at"],
                resolution_reason="migrated from resolved AgentTask" if row["status"] == "resolved" else "",
                state_hash=state_hash,
                metadata_json=json.dumps(payload, ensure_ascii=False),
                created_at=row["first_seen_at"] or now,
                updated_at=row["last_seen_at"] or now,
            )
        )


def _migrate_agent_memories(bind) -> None:
    inspector = sa.inspect(bind)
    if "agent_memories" not in inspector.get_table_names():
        return
    memories = sa.table(
        "agent_memories",
        sa.column("id", sa.Integer()),
        sa.column("scope_type", sa.String()),
        sa.column("scope_id", sa.String()),
        sa.column("kind", sa.String()),
        sa.column("content", sa.Text()),
        sa.column("source", sa.String()),
        sa.column("status", sa.String()),
        sa.column("created_at", sa.DateTime()),
        sa.column("updated_at", sa.DateTime()),
    )
    preferences = Base.metadata.tables["owner_preferences"]
    skills = Base.metadata.tables["ai_skills"]
    now = datetime.utcnow()
    for row in bind.execute(sa.select(memories).where(memories.c.status == "active")).mappings():
        if row["kind"] == "preference":
            exists = bind.execute(
                sa.select(preferences.c.id).where(preferences.c.legacy_memory_id == row["id"])
            ).scalar()
            if not exists:
                bind.execute(
                    preferences.insert().values(
                        scope="global" if row["scope_type"] == "owner" else f"{row['scope_type']}:{row['scope_id']}",
                        key=f"legacy_preference_{row['id']}",
                        value_json=json.dumps({"text": row["content"] or ""}, ensure_ascii=False),
                        source=row["source"] or "legacy_memory",
                        mode="persistent",
                        enabled=True,
                        legacy_memory_id=row["id"],
                        created_at=row["created_at"] or now,
                        updated_at=row["updated_at"] or now,
                    )
                )
        elif row["kind"] == "skill":
            exists = bind.execute(sa.select(skills.c.id).where(skills.c.legacy_memory_id == row["id"])).scalar()
            if not exists:
                bind.execute(
                    skills.insert().values(
                        name=f"legacy-skill-{row['id']}",
                        description=row["content"] or "",
                        goal=row["content"] or "",
                        trigger_type="manual",
                        trigger_config_json="{}",
                        preconditions_json="[]",
                        steps_json="[]",
                        allowed_tools_json="[]",
                        autonomous_tools_json="[]",
                        confirmation_required_tools_json="[]",
                        success_metric_json="{}",
                        scope="global",
                        version=1,
                        status="draft",
                        created_by="legacy_memory",
                        legacy_memory_id=row["id"],
                        created_at=row["created_at"] or now,
                        updated_at=row["updated_at"] or now,
                    )
                )


def upgrade() -> None:
    bind = op.get_bind()
    _create_new_tables(bind)
    _add_proposal_columns(bind)
    _migrate_agent_tasks(bind)
    _migrate_agent_memories(bind)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    proposal_columns = {column["name"] for column in inspector.get_columns("agent_action_proposals")}
    for column in reversed(PROPOSAL_COLUMNS):
        if column.name in proposal_columns:
            op.drop_column("agent_action_proposals", column.name)
    inspector = sa.inspect(bind)
    for table_name in reversed(NEW_TABLES):
        if table_name in inspector.get_table_names():
            op.drop_table(table_name)
