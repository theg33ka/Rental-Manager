from __future__ import annotations

from datetime import date, datetime, timedelta
import json
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from rental_manager.database import Base
from rental_manager.main import api_hermes_cases, api_hermes_commitments, api_hermes_control_center
from rental_manager.models import (
    AiConversation,
    AiFeatureUsageDaily,
    AgentActionProposal,
    DomainEvent,
    HermesAgentRun,
    Lease,
    OperationalCase,
    PaymentSituation,
    RentalObject,
    ReminderOutcome,
    RentCharge,
    Tenant,
    TenantStrategyProfile,
    Apartment,
)
from rental_manager.services.hermes.briefing import build_owner_briefing, mark_briefing_sent
from rental_manager.services.hermes.cases import reconcile_operational_cases
from rental_manager.services.hermes.events import emit_domain_event, stable_hash
from rental_manager.services.hermes.memory import (
    applicable_preferences,
    capture_owner_preference,
    create_owner_commitment,
    is_commitment_phrase,
)
from rental_manager.services.hermes.reminders import (
    record_reminder_outcome,
    reminder_allowed,
    tenant_strategy,
)
from rental_manager.services.hermes.runtime import (
    GroupedProposalPlan,
    build_owner_context,
    build_tenant_context,
    complete_agent_run,
    create_agent_run,
    create_grouped_deferral_proposal,
    execute_grouped_deferral,
    usage_summary,
    unchanged_analysis_exists,
)
from rental_manager.services.hermes.safety import ActionSafetyRegistry


class HermesCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        db_path = Path(self.tmp.name) / "hermes.db"
        self.engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False, future=True)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.tmp.cleanup()

    @staticmethod
    def add_lease(
        session,
        *,
        object_name: str = "БД",
        apartment_name: str = "3",
        tenant_name: str = "Иван",
        amount: float = 30_000,
    ) -> tuple[Lease, RentCharge]:
        rental_object = RentalObject(name=object_name, short_code=object_name)
        apartment = Apartment(object=rental_object, name=apartment_name)
        tenant = Tenant(full_name=tenant_name)
        lease = Lease(
            apartment=apartment,
            tenant=tenant,
            start_date=date(2026, 1, 1),
            payment_day=5,
            ip_amount=amount,
            personal_amount=0,
            active=True,
        )
        charge = RentCharge(
            lease=lease,
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
            due_date=date(2026, 7, 5),
            ip_due=amount,
            personal_due=0,
            status="pending",
        )
        session.add_all([lease, charge])
        session.flush()
        return lease, charge

    @staticmethod
    def add_case(
        session,
        *,
        key: str,
        priority: float,
        property_id: int | None = None,
        apartment_id: int | None = None,
        contract_id: int | None = None,
        status: str = "active",
        case_type: str = "custom_operational_issue",
    ) -> OperationalCase:
        state_hash = stable_hash({"key": key, "version": 1})
        item = OperationalCase(
            case_key=key,
            case_type=case_type,
            status=status,
            severity="high",
            priority_score=priority,
            property_id=property_id,
            apartment_id=apartment_id,
            contract_id=contract_id,
            title=f"Вопрос {key}",
            compact_summary=(f"Ситуация {key} требует внимания " * 4).strip(),
            state_hash=state_hash,
            waiting_for="owner" if status == "waiting_owner" else "",
        )
        session.add(item)
        session.flush()
        return item

    def test_briefing_has_three_cases_limit_and_groups_by_property(self) -> None:
        with self.Session() as session:
            lease, _charge = self.add_lease(session)
            for number in range(4):
                self.add_case(
                    session,
                    key=f"briefing:{number}",
                    priority=100 - number,
                    property_id=lease.apartment.object_id,
                    apartment_id=lease.apartment_id,
                )

            build = build_owner_briefing(session, today=date(2026, 7, 15), persist=False, force=True)

            self.assertEqual(len(build.cases), 3)
            self.assertLessEqual(len(build.text), 800)
            self.assertNotIn("|", build.text)
            self.assertIn("БД", build.text)
            self.assertGreaterEqual(build.remaining_count, 1)

    def test_unchanged_waiting_case_is_not_repeated(self) -> None:
        with self.Session() as session:
            item = self.add_case(session, key="waiting", priority=90, status="waiting_owner")
            first = build_owner_briefing(session, today=date(2026, 7, 15), persist=True, force=True)
            self.assertEqual([case.id for case in first.cases], [item.id])
            mark_briefing_sent(first)
            session.flush()

            second = build_owner_briefing(session, today=date(2026, 7, 16), persist=False, force=True)

            self.assertEqual(second.cases, [])
            self.assertEqual(second.text, "")

    def test_commitment_phrase_sets_waiting_owner_and_due_briefing_asks_result(self) -> None:
        with self.Session() as session:
            item = self.add_case(session, key="commitment", priority=90)
            text = "Я разберусь"
            self.assertTrue(is_commitment_phrase(text))
            commitment = create_owner_commitment(
                session,
                case=item,
                text=text,
                now=datetime(2026, 7, 15, 11, 0),
                briefing_time="10:00",
            )
            self.assertEqual(commitment.due_at, datetime(2026, 7, 16, 10, 0))
            self.assertEqual(item.status, "waiting_owner")
            commitment.due_at = datetime(2026, 7, 14, 10, 0)
            item.next_review_at = commitment.due_at

            build = build_owner_briefing(session, today=date(2026, 7, 15), persist=False, force=True)

            self.assertIn("получилось разобраться", build.text.lower())

    def test_paid_charge_closes_case_and_broken_promise_updates_strategy_once(self) -> None:
        with self.Session() as session:
            lease, charge = self.add_lease(session)
            open_cases = reconcile_operational_cases(session, date(2026, 7, 15))
            rent_case = next(item for item in open_cases if item.case_key == f"rent:{charge.id}")
            charge.ip_paid = charge.ip_due
            charge.status = "paid"
            reconcile_operational_cases(session, date(2026, 7, 15))
            self.assertEqual(rent_case.status, "resolved")

            situation = PaymentSituation(
                lease_id=lease.id,
                kind="rent",
                reference_id=charge.id,
                status="promised",
                promise_date=date(2026, 7, 14),
            )
            session.add(situation)
            session.flush()
            reconcile_operational_cases(session, date(2026, 7, 15))
            reconcile_operational_cases(session, date(2026, 7, 15))
            broken_case = session.scalar(
                select(OperationalCase).where(OperationalCase.case_key == f"broken-promise:{situation.id}")
            )
            strategy = session.scalar(
                select(TenantStrategyProfile).where(TenantStrategyProfile.contract_id == lease.id)
            )

            self.assertEqual(broken_case.case_type, "broken_payment_promise")
            self.assertEqual(strategy.broken_promises_count, 1)

    def test_one_shot_preference_is_parsed_consumed_once_and_persistent_remains(self) -> None:
        with self.Session() as session:
            one_shot = capture_owner_preference(
                session,
                "В следующем аудите не упоминай расходы",
            )
            persistent = capture_owner_preference(session, "Всегда группируй по объектам")
            self.assertEqual(one_shot.mode, "once")
            self.assertEqual(one_shot.key, "exclude_categories")
            self.assertEqual(persistent.mode, "persistent")
            self.add_case(session, key="pref", priority=50)

            build = build_owner_briefing(session, today=date(2026, 7, 15), persist=True, force=True)
            mark_briefing_sent(build)
            session.flush()

            self.assertFalse(one_shot.enabled)
            self.assertIsNotNone(one_shot.consumed_at)
            active = applicable_preferences(session, scopes=["daily_briefing"])
            self.assertIn(persistent.id, [item.id for item in active])
            self.assertNotIn(one_shot.id, [item.id for item in active])

    def test_owner_context_is_case_scoped_and_honors_output_limit(self) -> None:
        with self.Session() as session:
            lease, _charge = self.add_lease(session, object_name="БД", apartment_name="3")
            selected_case = self.add_case(
                session,
                key="selected",
                priority=90,
                property_id=lease.apartment.object_id,
                apartment_id=lease.apartment_id,
                contract_id=lease.id,
            )
            self.add_case(session, key="other", priority=80)

            context = build_owner_context(
                session,
                chat_id=99,
                user_text=f"case {selected_case.id}",
                model="deepseek-chat",
                output_limit=321,
            )

            self.assertEqual(context.case_ids, [selected_case.id])
            self.assertEqual(context.manifest.output_limit, 321)
            self.assertIn("full dashboard", context.manifest.excluded_context)
            payload = json.loads(context.text)
            self.assertEqual(len(payload["cases"]), 1)

    def test_tenant_context_contains_only_current_contract(self) -> None:
        with self.Session() as session:
            lease_one, _ = self.add_lease(session, object_name="Дом A", apartment_name="1", tenant_name="Первый")
            lease_two, _ = self.add_lease(session, object_name="Дом B", apartment_name="2", tenant_name="Второй")

            context = build_tenant_context(
                session,
                lease=lease_one,
                user_text="Сколько платить?",
                model="deepseek-chat",
                reason="tenant question",
                output_limit=240,
            )
            payload = json.loads(context.text)

            self.assertEqual(payload["contract"]["id"], lease_one.id)
            self.assertNotIn(lease_two.id, context.manifest.included_entities["contracts"])
            self.assertEqual(context.manifest.output_limit, 240)
            self.assertNotIn("Второй", context.text)

    def test_deterministic_reminder_has_duplicate_and_deferral_guards(self) -> None:
        with self.Session() as session:
            lease, charge = self.add_lease(session)
            situation = PaymentSituation(
                lease_id=lease.id,
                kind="rent",
                reference_id=charge.id,
                status="awaiting_payment",
            )
            session.add(situation)
            session.flush()
            now = datetime(2026, 7, 15, 12, 0)
            allowed, reason = reminder_allowed(session, situation=situation, now=now)
            self.assertEqual((allowed, reason), (True, "allowed"))

            record_reminder_outcome(
                session,
                lease_id=lease.id,
                stage="soft_overdue",
                template_key="rent_soft",
                payment_situation_id=situation.id,
                sent_at=now,
            )
            session.flush()
            allowed, reason = reminder_allowed(session, situation=situation, now=now)
            self.assertEqual((allowed, reason), (False, "already_sent_today"))

            situation.paused_until = now.date() + timedelta(days=3)
            allowed, reason = reminder_allowed(session, situation=situation, now=now + timedelta(days=1))
            self.assertEqual((allowed, reason), (False, "active_deferral"))
            self.assertEqual(session.scalar(select(func.count(ReminderOutcome.id))), 1)

    def test_grouped_proposal_is_single_and_execution_is_atomic(self) -> None:
        with self.Session() as session:
            lease, charge = self.add_lease(session)
            conversation = AiConversation(chat_id="99", role="owner")
            session.add(conversation)
            session.flush()
            plan = GroupedProposalPlan(
                matched=True,
                lease_ids=[lease.id],
                items=[
                    {"kind": "rent", "id": charge.id, "lease_id": lease.id, "amount": charge.ip_due},
                    {"kind": "rent", "id": 999999, "lease_id": lease.id, "amount": 1},
                ],
                new_date="2026-07-22",
                total_amount=charge.ip_due + 1,
                preview="Одна пакетная отсрочка",
            )
            proposal = create_grouped_deferral_proposal(
                session,
                conversation=conversation,
                owner_chat_id=99,
                plan=plan,
            )
            duplicate = create_grouped_deferral_proposal(
                session,
                conversation=conversation,
                owner_chat_id=99,
                plan=plan,
            )
            self.assertEqual(proposal.id, duplicate.id)
            self.assertEqual(session.scalar(select(func.count(AgentActionProposal.id))), 1)

            with self.assertRaises(ValueError):
                execute_grouped_deferral(session, proposal, today=date(2026, 7, 15))
            self.assertIsNone(charge.deferral_until)

    def test_safety_policy_cannot_be_bypassed_by_skills(self) -> None:
        registry = ActionSafetyRegistry(mass_action_threshold=2)
        critical = registry.classify("delete_lease")
        mass = registry.classify("grouped_deferral", {"target_count": 3})
        unknown = registry.classify("invented_irreversible_tool", owner_level_one_enabled=True)
        errors = registry.validate_skill(
            {
                "allowed_tools": ["delete_lease"],
                "autonomous_tools": ["delete_lease"],
                "confirmation_required_tools": [],
            }
        )

        self.assertTrue(critical.confirmation_required)
        self.assertFalse(critical.autonomous_allowed)
        self.assertEqual(mass.level, 3)
        self.assertFalse(unknown.autonomous_allowed)
        self.assertTrue(errors)

    def test_unchanged_state_hash_skips_repeat_ai_analysis(self) -> None:
        with self.Session() as session:
            manifest_context = build_owner_context(
                session,
                chat_id=99,
                user_text="Подробный аудит",
                model="deepseek-chat",
                feature="deep_audit",
            )
            run = create_agent_run(session, manifest=manifest_context.manifest, trigger="manual")
            complete_agent_run(run)
            session.flush()

            self.assertTrue(
                unchanged_analysis_exists(
                    session,
                    feature="deep_audit",
                    state_hash=manifest_context.manifest.state_hash,
                    trigger="manual",
                )
            )
            self.assertEqual(session.scalar(select(func.count(HermesAgentRun.id))), 1)

    def test_domain_events_are_idempotent_with_full_audit_fields(self) -> None:
        with self.Session() as session:
            first = emit_domain_event(
                session,
                "charge_changed",
                entity_type="RentCharge",
                entity_id=42,
                payload={"status": "partial"},
                actor_type="owner",
                source="web",
                correlation_id="corr-1",
                idempotency_key="same-event",
            )
            second = emit_domain_event(
                session,
                "charge_changed",
                entity_type="RentCharge",
                entity_id=42,
                payload={"status": "partial"},
                actor_type="owner",
                source="web",
                correlation_id="corr-1",
                idempotency_key="same-event",
            )
            session.flush()

            self.assertIs(first, second)
            self.assertEqual(session.scalar(select(func.count(DomainEvent.id))), 1)
            self.assertEqual(first.actor_type, "owner")
            self.assertEqual(first.source, "web")
            self.assertEqual(first.correlation_id, "corr-1")

    def test_usage_summary_separates_today_from_month_totals(self) -> None:
        with self.Session() as session:
            session.add_all(
                [
                    AiFeatureUsageDaily(
                        usage_date=date(2026, 7, 14),
                        feature="owner_chat",
                        provider="deepseek",
                        model="chat",
                        calls=3,
                        total_tokens=300,
                        cost_rub=3.5,
                    ),
                    AiFeatureUsageDaily(
                        usage_date=date(2026, 7, 15),
                        feature="owner_chat",
                        provider="deepseek",
                        model="chat",
                        calls=2,
                        total_tokens=200,
                        cost_rub=2.5,
                    ),
                ]
            )
            session.flush()

            owner_usage = usage_summary(session, today=date(2026, 7, 15))["features"]["owner_chat"]

            self.assertEqual(owner_usage["calls"], 5)
            self.assertEqual(owner_usage["today_calls"], 2)
            self.assertEqual(owner_usage["today_tokens"], 200)
            self.assertEqual(owner_usage["today_cost_rub"], 2.5)

    def test_android_aliases_expose_hermes_without_changing_existing_models(self) -> None:
        with self.Session() as session:
            lease, _ = self.add_lease(session)
            case = self.add_case(
                session,
                key="android",
                priority=70,
                property_id=lease.apartment.object_id,
                apartment_id=lease.apartment_id,
                contract_id=lease.id,
            )
            create_owner_commitment(session, case=case, text="Проверю завтра")
            tenant_strategy(session, lease)
            session.commit()

            cases = api_hermes_cases(session=session)
            commitments = api_hermes_commitments(session=session)
            summary = api_hermes_control_center(session=session)

            self.assertIn(case.id, [item["id"] for item in cases])
            self.assertTrue(commitments)
            self.assertIn("overview", summary)
            self.assertIn("settings", summary)


if __name__ == "__main__":
    unittest.main()
