from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from rental_manager.database import Base
from rental_manager.main import (
    agent_memory_context,
    agent_tenant_state,
    handle_agent_callback_query,
    handle_telegram_message,
    normalize_agent_action,
    run_tenant_rent_dialogue,
    sync_agent_tasks,
    update_tenant_state_from_envelope,
)
from rental_manager.models import (
    AgentActionProposal,
    AgentMemory,
    AgentTask,
    AiConversation,
    Apartment,
    AppSetting,
    Lease,
    RentalObject,
    RentCharge,
    Tenant,
    utc_now,
)
from rental_manager.services.agent_protocol import AgentEnvelope, parse_agent_envelope


class AgentRuntimeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        db_path = Path(self.tmp.name) / "agent.db"
        self.engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, future=True)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.tmp.cleanup()

    def create_lease(self, session, *, tenant_name: str = "Иван", apartment_name: str = "1") -> tuple[Lease, RentCharge]:
        rental_object = RentalObject(name=f"Дом {apartment_name}")
        apartment = Apartment(object=rental_object, name=apartment_name)
        tenant = Tenant(full_name=tenant_name)
        lease = Lease(
            apartment=apartment,
            tenant=tenant,
            start_date=date.today() - timedelta(days=90),
            payment_day=1,
            ip_amount=10_000,
            personal_amount=2_000,
        )
        charge = RentCharge(
            lease=lease,
            period_start=date.today().replace(day=1),
            period_end=date.today(),
            due_date=date.today() - timedelta(days=2),
            ip_due=10_000,
            personal_due=2_000,
        )
        session.add_all([rental_object, apartment, tenant, lease, charge])
        session.flush()
        return lease, charge


class AgentProtocolTests(unittest.TestCase):
    def test_parses_fenced_json_action(self) -> None:
        envelope = parse_agent_envelope(
            """```json
            {"reply":"Подготовил.","actions":[{"type":"defer_rent","payload":{"lease_id":1,"charge_id":2}}],"memory":[{"kind":"decision","content":"Проверять отсрочки вручную","importance":3}]}
            ```"""
        )

        self.assertEqual(envelope.reply, "Подготовил.")
        self.assertEqual(envelope.actions[0]["type"], "defer_rent")
        self.assertEqual(envelope.memories[0]["importance"], 3)

    def test_plain_text_remains_safe_reply_without_actions(self) -> None:
        envelope = parse_agent_envelope("Обычный ответ")

        self.assertEqual(envelope.reply, "Обычный ответ")
        self.assertEqual(envelope.actions, [])


class AgentActionTests(AgentRuntimeTestCase):
    def test_normalize_deferral_requires_real_charge(self) -> None:
        with self.Session() as session:
            lease, charge = self.create_lease(session)
            action = {
                "type": "defer_rent",
                "payload": {
                    "lease_id": lease.id,
                    "charge_id": charge.id,
                    "deferral_until": (date.today() + timedelta(days=5)).isoformat(),
                },
            }

            resolved_lease, action_type, payload, preview = normalize_agent_action(session, action)

        self.assertEqual(resolved_lease.id, lease.id)
        self.assertEqual(action_type, "defer_rent")
        self.assertEqual(payload["charge_id"], charge.id)
        self.assertIn("Предлагаю выдать отсрочку", preview)

    def test_owner_callback_executes_deferral_once(self) -> None:
        with self.Session() as session:
            lease, charge = self.create_lease(session)
            session.add(AppSetting(key="telegram_owner_chat_id", value="999"))
            conversation = AiConversation(chat_id="999", role="owner")
            session.add(conversation)
            session.flush()
            until = date.today() + timedelta(days=7)
            proposal = AgentActionProposal(
                conversation_id=conversation.id,
                lease_id=lease.id,
                action_type="defer_rent",
                status="pending",
                payload_json=json.dumps(
                    {
                        "lease_id": lease.id,
                        "charge_id": charge.id,
                        "deferral_until": until.isoformat(),
                        "note": "по просьбе владельца",
                        "notify_tenant": False,
                    }
                ),
                preview_text="Предлагаю выдать отсрочку",
                owner_chat_id="999",
                expires_at=utc_now() + timedelta(hours=1),
            )
            session.add(proposal)
            session.flush()
            callback = {
                "id": "callback-1",
                "data": f"agent:confirm:{proposal.id}",
                "from": {"id": 999},
                "message": {"message_id": 10, "chat": {"id": 999}},
            }

            with patch("rental_manager.main.telegram_token", return_value="token"), patch(
                "rental_manager.main.safe_answer_agent_callback"
            ), patch("rental_manager.main.send_telegram_text"), patch("rental_manager.main.clear_inline_keyboard"):
                handle_agent_callback_query(session, callback)
                handle_agent_callback_query(session, callback)

            self.assertEqual(proposal.status, "executed")
            self.assertEqual(charge.deferral_until, until)

    def test_non_owner_cannot_confirm(self) -> None:
        with self.Session() as session:
            lease, _charge = self.create_lease(session)
            session.add(AppSetting(key="telegram_owner_chat_id", value="999"))
            proposal = AgentActionProposal(
                lease_id=lease.id,
                action_type="send_tenant_message",
                status="pending",
                payload_json=json.dumps({"lease_id": lease.id, "text": "test"}),
                preview_text="message",
            )
            session.add(proposal)
            session.flush()

            with patch("rental_manager.main.telegram_token", return_value="token"), patch(
                "rental_manager.main.safe_answer_agent_callback"
            ) as mocked_answer:
                handle_agent_callback_query(
                    session,
                    {
                        "id": "callback-2",
                        "data": f"agent:confirm:{proposal.id}",
                        "from": {"id": 111},
                        "message": {"message_id": 11, "chat": {"id": 111}},
                    },
                )

            self.assertEqual(proposal.status, "pending")
            mocked_answer.assert_called_once()


class AgentMemoryAndDialogueTests(AgentRuntimeTestCase):
    def test_memory_context_is_scoped_to_lease(self) -> None:
        with self.Session() as session:
            first, _ = self.create_lease(session, tenant_name="Первый", apartment_name="1")
            second, _ = self.create_lease(session, tenant_name="Второй", apartment_name="2")
            session.add_all(
                [
                    AgentMemory(scope_type="lease", scope_id=str(first.id), lease_id=first.id, content="Оплатит в пятницу"),
                    AgentMemory(scope_type="lease", scope_id=str(second.id), lease_id=second.id, content="Просит ремонт"),
                ]
            )
            session.flush()

            context = agent_memory_context(
                session,
                scope_type="lease",
                scope_id=str(first.id),
                lease_id=first.id,
            )

        self.assertIn("Оплатит в пятницу", context)
        self.assertNotIn("Просит ремонт", context)

    def test_payment_promise_pauses_followup_until_after_date(self) -> None:
        with self.Session() as session:
            lease, charge = self.create_lease(session)
            state = agent_tenant_state(session, lease)
            promised = date.today() + timedelta(days=2)
            update_tenant_state_from_envelope(
                state,
                AgentEnvelope(reply="Хорошо", intent="payment_promise", promise_date=promised.isoformat()),
                "Оплачу через два дня",
            )

            with patch("rental_manager.main.send_tenant_message") as mocked_send:
                result = run_tenant_rent_dialogue(session, charge, date.today())

        self.assertEqual(result, "waiting_promise")
        mocked_send.assert_not_called()

    def test_supervisor_task_resolves_when_issue_disappears(self) -> None:
        with self.Session() as session:
            lease, charge = self.create_lease(session)
            dashboard = {
                "rent_overdue": [
                    {
                        "id": charge.id,
                        "lease_id": lease.id,
                        "tenant": lease.tenant.full_name,
                        "apartment": lease.apartment.name,
                        "debt": 12_000,
                        "due_date": charge.due_date.isoformat(),
                    }
                ]
            }
            sync_agent_tasks(session, dashboard)
            sync_agent_tasks(session, {})
            task = session.scalar(select(AgentTask).where(AgentTask.issue_key == f"rent:{charge.id}"))

        self.assertIsNotNone(task)
        self.assertEqual(task.status, "resolved")


class OwnerChatRoutingTests(AgentRuntimeTestCase):
    def test_plain_owner_text_goes_to_agent(self) -> None:
        with self.Session() as session:
            session.add(AppSetting(key="telegram_owner_chat_id", value="999"))
            session.flush()

            with patch("rental_manager.main.handle_owner_ai_message") as mocked_agent, patch(
                "rental_manager.main.send_telegram_text"
            ):
                handle_telegram_message(
                    session,
                    {"chat": {"id": 999}, "from": {"id": 999}, "text": "Кого сегодня надо пнуть?"},
                )

        mocked_agent.assert_called_once_with(session, 999, "Кого сегодня надо пнуть?")


if __name__ == "__main__":
    unittest.main()
