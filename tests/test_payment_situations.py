from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from rental_manager.database import Base
from rental_manager.main import (
    LOCAL_TZ,
    delete_lease,
    execute_owner_web_operation,
    handle_payment_situation_callback_query,
    handle_tenant_payment_text,
    parse_tenant_payment_date,
    payment_situation,
    render_message_text,
    run_due_reminders,
)
from rental_manager.models import (
    AgentActionProposal,
    Apartment,
    AppSetting,
    Lease,
    MessageLog,
    PaymentSituation,
    RentalObject,
    RentCharge,
    Tenant,
    UtilityBill,
    UtilityBillLine,
    UtilityService,
)


class PaymentSituationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        db_path = Path(self.tmp.name) / "payment-situations.db"
        self.engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, future=True)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.tmp.cleanup()

    def create_case(
        self,
        session,
        *,
        due_date: date | None = None,
        with_owner: bool = True,
    ) -> tuple[Lease, RentCharge]:
        today = date.today()
        rental_object = RentalObject(name="Дом")
        apartment = Apartment(object=rental_object, name="1", active=True)
        tenant = Tenant(full_name="Иван Иванов")
        lease = Lease(
            apartment=apartment,
            tenant=tenant,
            start_date=today - timedelta(days=90),
            payment_day=today.day,
            ip_amount=10_000,
            personal_amount=2_000,
            active=True,
        )
        charge = RentCharge(
            lease=lease,
            period_start=today.replace(day=1),
            period_end=today,
            due_date=due_date or today,
            ip_due=10_000,
            personal_due=2_000,
            status="pending",
        )
        session.add_all([rental_object, apartment, tenant, lease, charge])
        session.flush()
        settings = [
            AppSetting(key="telegram_bot_token", value="token"),
            AppSetting(key="telegram_tenant_links", value=json.dumps({str(tenant.id): "501"})),
            AppSetting(key="notifications_enabled", value="1"),
            AppSetting(key="notification_cutoff_date", value=(today - timedelta(days=30)).isoformat()),
            AppSetting(key="ip_recipient_name", value="ИП Петров Пётр Петрович"),
            AppSetting(key="ip_recipient_account", value="40802810000000000001"),
            AppSetting(key="personal_recipient_phone", value="+7 999 111-22-33"),
            AppSetting(key="personal_recipient_bank", value="Сбербанк"),
        ]
        if with_owner:
            settings.append(AppSetting(key="telegram_owner_chat_id", value="900"))
        session.add_all(settings)
        session.flush()
        return lease, charge

    def add_utility_line(
        self,
        session,
        lease: Lease,
        *,
        due_date: date,
        amount: float = 1500,
    ) -> UtilityBillLine:
        service = UtilityService(
            object=lease.apartment.object,
            kind="electricity",
            name="Электроэнергия",
        )
        bill = UtilityBill(
            service=service,
            period_start=due_date.replace(day=1),
            period_end=due_date,
            status="issued",
            total_cost=amount,
            due_date=due_date,
        )
        line = UtilityBillLine(
            bill=bill,
            apartment=lease.apartment,
            lease=lease,
            total_amount=amount,
            paid_amount=0,
            status="issued",
            due_date=due_date,
            note="расчётный период",
        )
        session.add_all([service, bill, line])
        session.flush()
        return line

    def test_message_examples_use_real_payment_channels(self) -> None:
        with self.Session() as session:
            lease, charge = self.create_case(session)
            line = self.add_utility_line(session, lease, due_date=date.today())

            rent_text = render_message_text(session, "message_rent_due", lease, charge=charge)
            utility_text = render_message_text(session, "message_utility_bill", lease, line=line)

        self.assertIn("ИП Петров Пётр Петрович", rent_text)
        self.assertIn("40802810000000000001", rent_text)
        self.assertIn("+7 999 111-22-33", rent_text)
        self.assertIn("отправьте оба чека", rent_text)
        self.assertIn("Коммуналка оплачивается переводом по номеру +7 999 111-22-33", utility_text)
        self.assertIn("Учтённые авансы уже вычтены", utility_text)

    def test_tenant_paid_button_pauses_reminders_until_receipt(self) -> None:
        with self.Session() as session:
            lease, charge = self.create_case(session)
            situation = payment_situation(session, "rent", charge.id, lease.id)
            callback = {
                "id": "callback-paid",
                "from": {"id": 501},
                "message": {"chat": {"id": 501}},
                "data": f"tenantpay:{situation.id}:paid",
            }

            with patch("rental_manager.main.safe_answer_agent_callback") as mocked_answer, patch(
                "rental_manager.main.send_telegram_text",
                return_value={"ok": True},
            ) as mocked_send:
                handled = handle_payment_situation_callback_query(session, callback)

            self.assertTrue(handled)
            self.assertEqual(situation.status, "awaiting_receipt")
            self.assertEqual(situation.tenant_response_count, 1)
            self.assertIn("Жду чек", mocked_answer.call_args.args[2])
            self.assertIn("отдельные чеки", mocked_send.call_args.args[2])

    def test_owner_situation_button_creates_confirmation_instead_of_mutating(self) -> None:
        with self.Session() as session:
            lease, charge = self.create_case(session)
            situation = payment_situation(session, "rent", charge.id, lease.id)
            callback = {
                "id": "callback-owner-manual",
                "from": {"id": 900},
                "message": {"chat": {"id": 900}},
                "data": f"ownersit:{situation.id}:manual",
            }

            with patch("rental_manager.main.safe_answer_agent_callback") as mocked_answer, patch(
                "rental_manager.main.send_telegram_text",
                return_value={"result": {"message_id": 88}},
            ):
                handled = handle_payment_situation_callback_query(session, callback)

            proposal = session.scalar(select(AgentActionProposal))
            self.assertTrue(handled)
            self.assertEqual(situation.mode, "normal")
            self.assertEqual(situation.status, "awaiting_payment")
            self.assertEqual(proposal.action_type, "set_payment_situation_mode")
            self.assertEqual(proposal.status, "pending")
            self.assertIn("подтверждение", mocked_answer.call_args.args[2])

    def test_short_deferral_is_applied_and_owner_is_notified(self) -> None:
        with self.Session() as session:
            lease, charge = self.create_case(session, due_date=date.today() - timedelta(days=1))
            situation = payment_situation(session, "rent", charge.id, lease.id)
            situation.status = "awaiting_payment_date"
            promised = date.today() + timedelta(days=2)

            with patch(
                "rental_manager.main.send_telegram_text",
                return_value={"ok": True},
            ) as mocked_send:
                handled = handle_tenant_payment_text(session, lease, "501", "Оплачу через 2 дня")

            recipients = [str(call.args[1]) for call in mocked_send.call_args_list]
            self.assertTrue(handled)
            self.assertEqual(situation.status, "short_deferral")
            self.assertEqual(situation.promise_date, promised)
            self.assertEqual(charge.deferral_until, promised)
            self.assertEqual(session.scalars(select(AgentActionProposal)).all(), [])
            self.assertIn("501", recipients)
            self.assertIn("900", recipients)

    def test_long_deferral_requires_owner_confirmation(self) -> None:
        with self.Session() as session:
            lease, charge = self.create_case(session, due_date=date.today() - timedelta(days=1))
            situation = payment_situation(session, "rent", charge.id, lease.id)
            situation.status = "awaiting_payment_date"
            promised = date.today() + timedelta(days=5)

            with patch(
                "rental_manager.main.send_telegram_text",
                return_value={"result": {"message_id": 77}},
            ):
                handled = handle_tenant_payment_text(session, lease, "501", "Оплачу через 5 дней")

            proposal = session.scalar(select(AgentActionProposal))
            payload = json.loads(proposal.payload_json)
            self.assertTrue(handled)
            self.assertEqual(situation.status, "escalated")
            self.assertEqual(charge.deferral_until, None)
            self.assertEqual(proposal.status, "pending")
            self.assertEqual(proposal.action_type, "defer_payment_situation")
            self.assertEqual(payload["promise_date"], promised.isoformat())

    def test_confirmed_payment_situation_deferral_updates_data(self) -> None:
        with self.Session() as session:
            lease, charge = self.create_case(session, due_date=date.today() - timedelta(days=1))
            situation = payment_situation(session, "rent", charge.id, lease.id)
            promised = date.today() + timedelta(days=5)

            with patch(
                "rental_manager.main.send_telegram_text",
                return_value={"ok": True},
            ) as mocked_send:
                result = execute_owner_web_operation(
                    session,
                    "defer_payment_situation",
                    {
                        "situation_id": situation.id,
                        "promise_date": promised.isoformat(),
                        "reason": "подтверждено тестом",
                        "notify_tenant": True,
                    },
                )

            self.assertIn("выполнена", result)
            self.assertEqual(situation.status, "promised")
            self.assertEqual(situation.promise_date, promised)
            self.assertEqual(charge.deferral_until, promised)
            self.assertEqual(str(mocked_send.call_args.args[1]), "501")

    def test_only_one_financial_notification_is_sent_per_tenant_per_day(self) -> None:
        today = date.today()
        with self.Session() as session:
            lease, charge = self.create_case(session, due_date=today, with_owner=False)
            utility_line = self.add_utility_line(session, lease, due_date=today)
            now = datetime.combine(today, time(hour=10, minute=30), tzinfo=LOCAL_TZ)

            with patch("rental_manager.main.local_now", return_value=now), patch(
                "rental_manager.main.send_telegram_text",
                return_value={"ok": True},
            ) as mocked_send, patch(
                "rental_manager.main.build_dashboard",
                return_value={},
            ), patch(
                "rental_manager.main.run_owner_payment_digest",
                return_value=0,
            ), patch(
                "rental_manager.main.run_owner_supervisor_digest",
                return_value=0,
            ), patch(
                "rental_manager.main.process_move_out_notifications",
                return_value={"processed": 0},
            ):
                first = run_due_reminders(session, today)
                second = run_due_reminders(session, today)

            situations = session.scalars(select(PaymentSituation).order_by(PaymentSituation.kind)).all()
            logs = session.scalars(
                select(MessageLog).where(MessageLog.note.like("auto-financial:%"))
            ).all()
            counts = {item.kind: item.notification_count for item in situations}

        self.assertEqual(first["sent"], 1)
        self.assertEqual(second["sent"], 0)
        self.assertEqual(mocked_send.call_count, 1)
        self.assertEqual(len(logs), 1)
        self.assertEqual(counts["rent"], 1)
        self.assertEqual(counts["utility"], 0)
        self.assertEqual(logs[0].rent_charge_id, charge.id)
        self.assertIsNone(logs[0].utility_line_id)
        self.assertNotEqual(utility_line.id, None)

    def test_promised_date_gets_one_followup_and_lease_deletion_cleans_situation(self) -> None:
        today = date.today()
        with self.Session() as session:
            lease, charge = self.create_case(session, due_date=today - timedelta(days=4), with_owner=False)
            situation = payment_situation(session, "rent", charge.id, lease.id)
            situation.status = "short_deferral"
            situation.promise_date = today
            situation.paused_until = today
            situation.notification_count = 2
            now = datetime.combine(today, time(hour=10, minute=30), tzinfo=LOCAL_TZ)

            with patch("rental_manager.main.local_now", return_value=now), patch(
                "rental_manager.main.send_telegram_text",
                return_value={"ok": True},
            ) as mocked_send, patch(
                "rental_manager.main.build_dashboard",
                return_value={},
            ), patch(
                "rental_manager.main.run_owner_payment_digest",
                return_value=0,
            ), patch(
                "rental_manager.main.run_owner_supervisor_digest",
                return_value=0,
            ), patch(
                "rental_manager.main.process_move_out_notifications",
                return_value={"processed": 0},
            ):
                first = run_due_reminders(session, today)
                second = run_due_reminders(session, today)

            log = session.scalar(select(MessageLog).where(MessageLog.note.like("%promise_followup%")))
            self.assertEqual(first["sent"], 1)
            self.assertEqual(second["sent"], 0)
            self.assertEqual(mocked_send.call_count, 1)
            self.assertEqual(situation.notification_count, 3)
            self.assertIsNotNone(log)

            delete_lease(lease.id, session)
            self.assertIsNone(session.get(PaymentSituation, situation.id))

    def test_payment_date_parser_understands_tenant_phrasing(self) -> None:
        today = date(2026, 6, 24)
        self.assertEqual(parse_tenant_payment_date("завтра", today), date(2026, 6, 25))
        self.assertEqual(parse_tenant_payment_date("оплачу через 3 дня", today), date(2026, 6, 27))
        self.assertEqual(parse_tenant_payment_date("смогу 29.06", today), date(2026, 6, 29))
        self.assertEqual(parse_tenant_payment_date("2026-07-01", today), date(2026, 7, 1))


if __name__ == "__main__":
    unittest.main()
