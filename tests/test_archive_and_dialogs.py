import json
from datetime import date, datetime, timedelta
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import select

from rental_manager import main
from rental_manager.models import (
    AiConversation, AiMessage, Apartment, AppSetting, Lease, MessageLog,
    RentalObject, RentCharge, Tenant, UtilityBill, UtilityBillLine, UtilityService,
)
from rental_manager.services.billing import generate_rent_charges, utility_amount
from rental_manager.services.payment_calendar import payment_calendar, payment_calendar_summary
from tests.test_billing import DatabaseTestCase


class ArchiveAndDialogTests(DatabaseTestCase):
    def fixture(self, session, moved=False):
        obj = RentalObject(name="Архивный дом")
        apartment = Apartment(object=obj, name="1", odn_share_percent=100)
        tenant = Tenant(full_name="Жилец с долгом")
        old = Lease(apartment=apartment, tenant=tenant, start_date=date(2026, 4, 1),
                    end_date=date(2026, 4, 30), active=False, payment_day=1,
                    ip_amount=1000, personal_amount=200)
        service = UtilityService(object=obj, name="Вода", kind="water")
        charge = RentCharge(lease=old, period_start=date(2026, 4, 1), period_end=date(2026, 4, 30),
                            due_date=date(2026, 4, 1), ip_due=1000, personal_due=200, ip_paid=100)
        bill = UtilityBill(service=service, period_start=date(2026, 4, 1), period_end=date(2026, 5, 1),
                           status="issued", due_date=date(2026, 5, 5))
        line = UtilityBillLine(bill=bill, apartment=apartment, lease=old, total_amount=1232,
                               paid_amount=200, status="issued", due_date=date(2026, 5, 5))
        session.add_all([charge, line])
        session.flush()
        main.set_lease_ignored(session, old.id, True)
        session.add(AppSetting(key="telegram_tenant_links", value=json.dumps({str(tenant.id): "456"})))
        current = old
        if moved:
            current = Lease(apartment=Apartment(object=obj, name="2"), tenant=tenant,
                            start_date=date(2026, 5, 1), active=True, payment_day=1)
            session.add(current)
        session.commit()
        return old, current, charge, line

    def test_archived_departed_debts_remain_in_dashboard_and_calendar(self):
        with self.Session() as session:
            old, _, charge, line = self.fixture(session)
            dashboard = main.build_dashboard(session)
            self.assertEqual(dashboard["expected_receipts"]["rent"], 1100)
            self.assertEqual(dashboard["expected_receipts"]["utility"], 1032)
            for mode, amount in [("rent", 1100), ("utility", 1032)]:
                payload = payment_calendar(session, date(2026, 4, 1), date(2026, 4, 30),
                                           mode=mode, ignored_lease_ids={old.id})
                row = payload["objects"][0]["apartments"][0]
                self.assertEqual(row["entries"][0]["debt"], amount)
                summary = payment_calendar_summary(session, mode=mode, ignored_lease_ids={old.id})
                self.assertTrue(summary["apartments"][0]["issues"])
            rows = main.rent_charges_payload("2026-04-01", "2026-04-30", session, generate_missing=False)
            self.assertEqual([row["id"] for row in rows], [charge.id])
            self.assertEqual(line.total_amount, 1232)
            session.flush()
            self.assertEqual(main.build_status_dashboard_fast(session)["rent_partial"], [None])

    def test_archive_preserves_old_debts_after_move_and_stops_automation(self):
        with self.Session() as session:
            old, current, _, _ = self.fixture(session, moved=True)
            text = main.build_all_debts_breakdown(session, current)
            self.assertIn("1 100,00", text)
            self.assertIn("1 032,00", text)
            old.active = True
            old.end_date = None
            session.flush()
            generate_rent_charges(session, until=date(2026, 6, 1))
            archived_charges = session.scalars(select(RentCharge).where(RentCharge.lease_id == old.id)).all()
            self.assertEqual(len(archived_charges), 1)
            with patch.object(main, "send_message") as send:
                with self.assertRaises(HTTPException):
                    main.send_tenant_message(session, old, "custom", custom_text="Тест")
                send.assert_not_called()
            self.assertFalse(main.reminder_meta(session, old, date.today())["eligible_auto"])

    def test_archived_current_month_expected_charge_is_hidden_from_month_summary(self):
        with self.Session() as session:
            old, current, _, _ = self.fixture(session, moved=True)
            archived = RentCharge(
                lease=old, period_start=date(2026, 9, 1), period_end=date(2026, 9, 30),
                due_date=date(2026, 9, 21), ip_due=1000, personal_due=21000,
            )
            current_charge = RentCharge(
                lease=current, period_start=date(2026, 9, 1), period_end=date(2026, 9, 30),
                due_date=date(2026, 9, 12), ip_due=1000, personal_due=12000,
            )
            session.add_all([archived, current_charge])
            session.flush()
            summary = main.month_dashboard_summary(session, 2026, 9, today=date(2026, 9, 21))
            self.assertEqual(summary["salary_due"], 12000)
            archived.due_date = date(2026, 9, 20)
            archived.personal_paid = 5000
            self.assertTrue(main.month_charge_visible(session, archived, date(2026, 9, 21)))
            archived.personal_paid = 21000
            archived.ip_paid = 1000
            self.assertFalse(main.month_charge_visible(session, archived, date(2026, 9, 21)))
            self.assertTrue(main.month_charge_visible(session, archived, date(2026, 10, 1)))
            self.assertEqual(session.get(RentCharge, archived.id).personal_paid, 21000)

    def test_month_progress_returns_only_current_salary_charge_for_replaced_tenant(self):
        with self.Session() as session:
            old, current, _, _ = self.fixture(session, moved=True)
            archived = RentCharge(
                lease=old, period_start=date(2026, 9, 1), period_end=date(2026, 9, 30),
                due_date=date(2026, 9, 21), ip_due=1000, personal_due=21000,
            )
            current_charge = RentCharge(
                lease=current, period_start=date(2026, 9, 1), period_end=date(2026, 9, 30),
                due_date=date(2026, 9, 12), ip_due=1000, personal_due=12000,
            )
            session.add_all([archived, current_charge])
            session.flush()
            payload = main.api_month_progress(2026, 9, session)
            self.assertEqual([item["id"] for item in payload["rent_charges"]], [current_charge.id])
            self.assertTrue(payload["rent_charges"][0]["current_payment"])

    def test_archived_chat_blocks_outgoing_but_current_contract_can_receive(self):
        with self.Session() as session:
            old, _, _, _ = self.fixture(session)
            with patch.object(main, "send_message") as send:
                with self.assertRaises(HTTPException):
                    main.send_telegram_text(session, "456", "Принял платёж")
                send.assert_not_called()
            current = Lease(apartment=old.apartment, tenant=old.tenant,
                            start_date=date(2026, 5, 1), payment_day=1, active=True)
            session.add(current)
            session.flush()
            with patch.object(main, "telegram_token", return_value="test"), patch.object(main, "send_message", return_value={"ok": True}):
                main.send_telegram_text(session, "456", "Принял платёж")
            payload = main.bot_dialog_messages_payload(session, f"lease:{current.id}")
            self.assertEqual([item["text"] for item in payload["messages"]], ["Принял платёж"])
            self.assertEqual(session.scalar(select(MessageLog)).lease_id, current.id)

    def test_full_history_includes_old_contract_without_duplicate_ai_reply(self):
        with self.Session() as session:
            old, current, _, _ = self.fixture(session, moved=True)
            conversation = AiConversation(chat_id="previous-chat", role="tenant", lease_id=old.id, tenant_id=old.tenant_id)
            session.add(conversation)
            session.flush()
            stamp = datetime(2026, 4, 1)
            session.add_all([
                MessageLog(lease_id=old.id, channel="telegram", status="sent", recipient_chat_id="previous-chat",
                           text=f"Сообщение {i}", created_at=stamp + timedelta(minutes=i))
                for i in range(230)
            ])
            session.add(AiMessage(conversation_id=conversation.id, lease_id=old.id, role="assistant", channel="telegram",
                                  text="Сообщение 229", created_at=stamp + timedelta(minutes=229)))
            session.flush()
            messages = main.bot_dialog_messages_payload(session, f"lease:{current.id}")["messages"]
            self.assertEqual(len(messages), 230)
            self.assertEqual(messages[0]["text"], "Сообщение 0")
            self.assertEqual(messages[-1]["text"], "Сообщение 229")

    def test_template_send_records_one_message(self):
        with self.Session() as session:
            _, current, _, _ = self.fixture(session, moved=True)
            with patch.object(main, "telegram_token", return_value="test"), patch.object(main, "send_message", return_value={"ok": True}):
                main.send_tenant_message(session, current, "custom", custom_text="Напоминание")
            messages = main.bot_dialog_messages_payload(session, f"lease:{current.id}")["messages"]
            self.assertEqual([item["text"] for item in messages], ["Напоминание"])

    def test_transfer_from_archive_keeps_old_debt_and_activates_new_contract(self):
        with self.Session() as session:
            old, _, charge, _ = self.fixture(session)
            target = Apartment(object=old.apartment.object, name="2")
            session.add(target)
            session.flush()
            result = main.transfer_lease(old.id, {
                "target_apartment_id": target.id, "transfer_date": "2026-05-01",
                "repeat_closed_transfer": True,
            }, session)
            self.assertTrue(result["old_lease"]["ignored"])
            self.assertFalse(result["new_lease"]["ignored"])
            self.assertEqual(session.get(RentCharge, charge.id).ip_due, 1000)

    def test_utility_rounds_up_to_ten_rubles(self):
        for original, expected in [(1232, 1240), (1240, 1240), (1240.01, 1250), (0, 0), (0.01, 10), (1239.99, 1240)]:
            with self.subTest(original=original):
                self.assertEqual(utility_amount(original), expected)
