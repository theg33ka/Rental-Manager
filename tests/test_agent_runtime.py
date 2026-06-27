from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from rental_manager.database import Base
from rental_manager.main import (
    agent_memory_context,
    agent_tenant_state,
    backfill_owner_style_preferences,
    build_dashboard,
    capture_owner_style_preferences,
    execute_agent_action,
    handle_agent_callback_query,
    handle_owner_ai_message,
    handle_telegram_message,
    normalize_agent_action,
    owner_agent_context,
    owner_debt_details_text,
    owner_request_allows_actions,
    owner_request_explicitly_requests_action,
    run_tenant_rent_dialogue,
    sync_agent_tasks,
    tenant_message_without_name,
    update_tenant_state_from_envelope,
)
from rental_manager.models import (
    AgentActionProposal,
    AgentMemory,
    AgentTask,
    AiConversation,
    AiMessage,
    Apartment,
    AppSetting,
    Lease,
    PaymentSituation,
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

    def test_parses_first_json_object_from_noisy_double_output(self) -> None:
        envelope = parse_agent_envelope(
            """```json
            {"reply":"Ок, подготовил к подтверждению.","actions":[{"type":"owner_operation","payload":{"operation":"run_reminders","arguments":{}},"reason":"владелец попросил"}],"memory":[]}
            ```
            ```json
            {"reply":"дубль, который нельзя отправлять в Telegram","actions":[]}
            ```"""
        )

        self.assertEqual(envelope.reply, "Ок, подготовил к подтверждению.")
        self.assertEqual(envelope.actions[0]["type"], "owner_operation")
        self.assertEqual(envelope.actions[0]["payload"]["operation"], "run_reminders")

    def test_malformed_protocol_json_is_not_sent_raw(self) -> None:
        envelope = parse_agent_envelope('```json\n{"reply":"Ок","actions":[{"type":"owner_operation"')

        self.assertIn("повреждённом JSON", envelope.reply)
        self.assertNotIn("```", envelope.reply)
        self.assertNotIn('"actions"', envelope.reply)
        self.assertEqual(envelope.actions, [])

    def test_parses_owner_operation(self) -> None:
        envelope = parse_agent_envelope(
            json.dumps(
                {
                    "reply": "Подготовил.",
                    "actions": [
                        {
                            "type": "owner_operation",
                            "payload": {
                                "operation": "create_expense",
                                "arguments": {
                                    "expense_date": "2026-06-24",
                                    "category": "ремонт",
                                    "amount": 1500,
                                },
                            },
                        }
                    ],
                },
                ensure_ascii=False,
            )
        )

        self.assertEqual(envelope.actions[0]["type"], "owner_operation")
        self.assertEqual(envelope.actions[0]["payload"]["operation"], "create_expense")

    def test_plain_text_remains_safe_reply_without_actions(self) -> None:
        envelope = parse_agent_envelope("Обычный ответ")

        self.assertEqual(envelope.reply, "Обычный ответ")
        self.assertEqual(envelope.actions, [])

    def test_parses_skill_memory(self) -> None:
        envelope = parse_agent_envelope(
            json.dumps(
                {
                    "reply": "Запомнил порядок.",
                    "memory": [
                        {
                            "kind": "skill",
                            "content": "Для коммунального должника сначала проверить строку счёта, затем подготовить сообщение без отправки.",
                            "importance": 3,
                        }
                    ],
                },
                ensure_ascii=False,
            )
        )

        self.assertEqual(envelope.memories[0]["kind"], "skill")
        self.assertEqual(envelope.memories[0]["importance"], 3)

    def test_tenant_message_removes_named_greeting(self) -> None:
        lease = Lease(tenant=Tenant(full_name="Никита Холобаев"))

        result = tenant_message_without_name(
            lease,
            "Уважаемый Никита Холобаев, напоминаем о задолженности.",
        )

        self.assertEqual(result, "Здравствуйте. Напоминаем о задолженности.")


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
                    "notify_tenant": False,
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
            situation = session.scalar(
                select(PaymentSituation).where(
                    PaymentSituation.kind == "rent",
                    PaymentSituation.reference_id == charge.id,
                )
            )
            dashboard = build_dashboard(session)

            self.assertIsNotNone(situation)
            self.assertEqual(situation.status, "promised")
            self.assertEqual(situation.promise_date, until)
            self.assertEqual(dashboard["rent_overdue"], [])
            self.assertEqual(dashboard["rent_deferred"][0]["id"], charge.id)

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

    def test_unlinked_tenant_message_proposal_is_rejected(self) -> None:
        with self.Session() as session:
            lease, _charge = self.create_lease(session)

            with self.assertRaises(HTTPException) as error:
                normalize_agent_action(
                    session,
                    {
                        "type": "send_tenant_message",
                        "payload": {"lease_id": lease.id, "text": "Здравствуйте. Оплатите долг."},
                    },
                )

        self.assertIn("/start", str(error.exception.detail))

    def test_message_cannot_claim_deferral_without_data_operation(self) -> None:
        with self.Session() as session:
            lease, _charge = self.create_lease(session)
            session.add(
                AppSetting(
                    key="telegram_tenant_links",
                    value=json.dumps({str(lease.tenant_id): "501"}),
                )
            )
            session.flush()

            with self.assertRaises(HTTPException) as error:
                normalize_agent_action(
                    session,
                    {
                        "type": "send_tenant_message",
                        "payload": {
                            "lease_id": lease.id,
                            "text": "Здравствуйте. Информируем вас об отсрочке платежа по аренде на 14 дней.",
                        },
                    },
                )

        self.assertIn("отдельная отправка сообщения не меняет данные", str(error.exception.detail))

    def test_existing_message_proposal_cannot_bypass_deferral_guard(self) -> None:
        with self.Session() as session:
            lease, _charge = self.create_lease(session)
            proposal = AgentActionProposal(
                lease_id=lease.id,
                action_type="send_tenant_message",
                status="approved",
                payload_json=json.dumps(
                    {
                        "lease_id": lease.id,
                        "text": "Здравствуйте. Владелец подтвердил отсрочку оплаты.",
                    },
                    ensure_ascii=False,
                ),
                preview_text="Предлагаю отправить сообщение",
            )
            session.add(proposal)
            session.flush()

            with self.assertRaises(HTTPException) as error:
                execute_agent_action(session, proposal)

        self.assertIn("Сначала подготовьте соответствующую операцию", str(error.exception.detail))

    def test_normalize_owner_operation_builds_detailed_preview(self) -> None:
        with self.Session() as session:
            lease, _charge = self.create_lease(session)

            resolved_lease, action_type, payload, preview = normalize_agent_action(
                session,
                {
                    "type": "owner_operation",
                    "payload": {
                        "operation": "set_lease_ignored",
                        "arguments": {"lease_id": lease.id, "ignored": True},
                    },
                },
            )

        self.assertEqual(resolved_lease.id, lease.id)
        self.assertEqual(action_type, "set_lease_ignored")
        self.assertTrue(payload["ignored"])
        self.assertIn("Порядок выполнения:", preview)
        self.assertIn("До подтверждения данные не изменятся.", preview)

    def test_owner_callback_executes_generic_web_operation(self) -> None:
        with self.Session() as session:
            lease, _charge = self.create_lease(session)
            session.add(AppSetting(key="telegram_owner_chat_id", value="999"))
            proposal = AgentActionProposal(
                lease_id=lease.id,
                action_type="set_lease_ignored",
                status="pending",
                payload_json=json.dumps({"lease_id": lease.id, "ignored": True}),
                preview_text="Предлагаю исключить договор из учёта",
                owner_chat_id="999",
                expires_at=utc_now() + timedelta(hours=1),
            )
            session.add(proposal)
            session.flush()

            with patch("rental_manager.main.telegram_token", return_value="token"), patch(
                "rental_manager.main.safe_answer_agent_callback"
            ), patch("rental_manager.main.send_telegram_text"), patch("rental_manager.main.clear_inline_keyboard"):
                handle_agent_callback_query(
                    session,
                    {
                        "id": "callback-generic",
                        "data": f"agent:confirm:{proposal.id}",
                        "from": {"id": 999},
                        "message": {"message_id": 12, "chat": {"id": 999}},
                    },
                )

            self.assertEqual(proposal.status, "executed")
            self.assertIn("IGNORE_LEASE_CONTROL", lease.notes)


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

    def test_owner_style_preference_is_saved_deterministically(self) -> None:
        with self.Session() as session:
            captured = capture_owner_style_preferences(
                session,
                "Не надо использовать имена и фамильярничать с жильцами.",
            )
            memories = session.scalars(select(AgentMemory)).all()

        self.assertEqual(len(captured), 2)
        self.assertEqual(len(memories), 2)
        self.assertTrue(all(memory.kind == "preference" for memory in memories))

    def test_owner_style_preference_is_backfilled_from_chat_history(self) -> None:
        with self.Session() as session:
            conversation = AiConversation(chat_id="999", role="owner")
            session.add(conversation)
            session.flush()
            session.add(
                AiMessage(
                    conversation_id=conversation.id,
                    role="user",
                    text="Не надо использовать имена жильцов.",
                )
            )
            session.flush()

            count = backfill_owner_style_preferences(session)
            memory = session.scalar(select(AgentMemory).where(AgentMemory.kind == "preference"))

        self.assertEqual(count, 1)
        self.assertIsNotNone(memory)
        self.assertIn("не обращаться по имени", memory.content)

    def test_owner_context_marks_unlinked_telegram_chat(self) -> None:
        with self.Session() as session:
            lease, _charge = self.create_lease(session)

            context = owner_agent_context(session, 999, {})

        self.assertIn(f"lease_id={lease.id}", context)
        self.assertIn("Telegram-чат: не привязан, сначала нужен /start", context)

    def test_debt_details_report_delivery_status(self) -> None:
        with self.Session() as session:
            lease, charge = self.create_lease(session)
            dashboard = {
                "utility_issued": [
                    {
                        "id": 1,
                        "lease_id": lease.id,
                        "object": lease.apartment.object.name,
                        "apartment": lease.apartment.name,
                        "tenant": lease.tenant.full_name,
                        "debt": 4500,
                        "due_date": charge.due_date.isoformat(),
                    }
                ]
            }

            answer = owner_debt_details_text(session, dashboard, "Кто и сколько должен по коммуналке?")

        self.assertIn(lease.tenant.full_name, answer)
        self.assertIn("4 500,00 ₽", answer)
        self.assertIn("Telegram не привязан: сначала нужен /start", answer)


class OwnerChatRoutingTests(AgentRuntimeTestCase):
    def test_owner_request_recognizes_plain_give_deferral_wording(self) -> None:
        text = "Дай отсрочку по коммуналке на 5 дней и уведомь жильца"

        self.assertTrue(owner_request_allows_actions(text))
        self.assertTrue(owner_request_explicitly_requests_action(text))

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

    def test_debt_question_never_sends_empty_answer_before_proposals(self) -> None:
        with self.Session() as session:
            lease, charge = self.create_lease(session)
            conversation = AiConversation(chat_id="999", role="owner")
            session.add(conversation)
            session.flush()
            dashboard = {
                "utility_issued": [
                    {
                        "id": 10,
                        "lease_id": lease.id,
                        "object": lease.apartment.object.name,
                        "apartment": lease.apartment.name,
                        "tenant": lease.tenant.full_name,
                        "debt": 3200,
                        "due_date": charge.due_date.isoformat(),
                    }
                ]
            }
            envelope = AgentEnvelope(
                reply="",
                actions=[{"type": "send_tenant_message", "payload": {"lease_id": lease.id, "text": "Напоминание"}}],
            )

            with patch("rental_manager.main.build_dashboard", return_value=dashboard), patch(
                "rental_manager.main.sync_agent_tasks"
            ), patch("rental_manager.main.owner_agent_context", return_value="context"), patch(
                "rental_manager.main.call_agent_envelope", return_value=(conversation, envelope)
            ), patch("rental_manager.main.store_agent_memories"), patch(
                "rental_manager.main.send_telegram_text"
            ) as mocked_send, patch("rental_manager.main.create_agent_action_proposal") as mocked_proposal:
                handle_owner_ai_message(session, 999, "Кто и сколько должен по коммуналке?")

        sent_text = mocked_send.call_args.args[2]
        self.assertIn("3 200,00 ₽", sent_text)
        self.assertIn("Telegram не привязан", sent_text)
        mocked_proposal.assert_called_once()

    def test_text_yes_never_confirms_pending_action(self) -> None:
        with self.Session() as session:
            session.add(AppSetting(key="telegram_owner_chat_id", value="999"))
            proposal = AgentActionProposal(
                action_type="run_reminders",
                status="pending",
                payload_json="{}",
                preview_text="Запустить напоминания",
                owner_chat_id="999",
            )
            session.add(proposal)
            session.flush()

            with patch("rental_manager.main.handle_owner_ai_message") as mocked_agent, patch(
                "rental_manager.main.send_telegram_text"
            ) as mocked_send:
                handle_telegram_message(
                    session,
                    {"chat": {"id": 999}, "from": {"id": 999}, "text": "да"},
                )

        mocked_agent.assert_not_called()
        self.assertIn("Подтверждение текстом не выполняет действия", mocked_send.call_args.args[2])
        self.assertEqual(proposal.status, "pending")

    def test_explicit_action_without_backend_proposal_is_reported_as_not_done(self) -> None:
        with self.Session() as session:
            conversation = AiConversation(chat_id="999", role="owner")
            session.add(conversation)
            session.flush()
            envelope = AgentEnvelope(reply="Я всё выполнил.", actions=[])

            with patch("rental_manager.main.build_dashboard", return_value={}), patch(
                "rental_manager.main.sync_agent_tasks"
            ), patch("rental_manager.main.owner_agent_context", return_value="context"), patch(
                "rental_manager.main.call_agent_envelope", return_value=(conversation, envelope)
            ), patch("rental_manager.main.store_agent_memories"), patch(
                "rental_manager.main.send_telegram_text"
            ) as mocked_send:
                handle_owner_ai_message(session, 999, "Запусти напоминания")

        sent_text = mocked_send.call_args.args[2]
        self.assertIn("Кнопки подтверждения не созданы", sent_text)
        self.assertIn("Ничего не изменено", sent_text)
        self.assertNotIn("Я всё выполнил", sent_text)

    def test_action_failure_suggests_supported_manual_debt_path(self) -> None:
        with self.Session() as session:
            conversation = AiConversation(chat_id="999", role="owner")
            session.add(conversation)
            session.flush()
            envelope = AgentEnvelope(
                reply="Подготовил.",
                actions=[
                    {
                        "type": "owner_operation",
                        "payload": {
                            "operation": "create_manual_debt",
                            "arguments": {
                                "title": "Долг за старую аренду",
                                "amount": 20000,
                                "due_date": date.today().isoformat(),
                            },
                        },
                    }
                ],
            )

            with patch("rental_manager.main.build_dashboard", return_value={}), patch(
                "rental_manager.main.sync_agent_tasks"
            ), patch("rental_manager.main.owner_agent_context", return_value="context"), patch(
                "rental_manager.main.call_agent_envelope", return_value=(conversation, envelope)
            ), patch("rental_manager.main.store_agent_memories"), patch(
                "rental_manager.main.send_telegram_text"
            ) as mocked_send:
                handle_owner_ai_message(session, 999, "Создай ручной долг Никите за аренду 20000")

        sent_text = mocked_send.call_args.args[2]
        self.assertIn("Кнопки подтверждения не созданы", sent_text)
        self.assertIn("Создать ручной долг", sent_text)
        self.assertIn("договор/квартира/жилец", sent_text)
        self.assertNotIn("```", sent_text)


if __name__ == "__main__":
    unittest.main()
