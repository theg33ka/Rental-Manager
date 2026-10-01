from __future__ import annotations

from datetime import date, datetime, timedelta
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import select

from rental_manager import main
from rental_manager.models import AgentActionProposal, AppSetting, CashPaymentRequest, PaymentProfile, PaymentReceipt
from rental_manager.services.cash_payments import offer_cash_payment
from rental_manager.services.hermes.safety import ACTION_SAFETY_REGISTRY
from tests import test_payment_situations


class CashPaymentTests(unittest.TestCase):
    setUp = test_payment_situations.PaymentSituationTests.setUp
    tearDown = test_payment_situations.PaymentSituationTests.tearDown
    create_case = test_payment_situations.PaymentSituationTests.create_case

    def case(self, session, *, both=False, **kwargs):
        lease, charge = self.create_case(session, **kwargs)
        profile = PaymentProfile(name="Наличные", personal_payment_method="cash", ip_payment_method="cash" if both else "transfer")
        lease.apartment.object.payment_profile = profile
        session.add(profile)
        session.flush()
        return lease, charge

    def callback(self, data, sender="501", chat="501"):
        return {"id": "test-callback", "data": data, "from": {"id": sender}, "message": {"chat": {"id": chat}}}

    def claim(self, session, charge):
        request = offer_cash_payment(session, charge, "501")
        main.handle_cash_payment_callback(session, self.callback(f"cashpay:{request.token}"))
        session.flush()
        self.assertEqual(request.status, "pending")
        return request, session.get(AgentActionProposal, request.proposal_id)

    def approve(self, session, proposal, *, sender="900", chat="900"):
        main.handle_agent_callback_query(session, self.callback(f"agent:confirm:{proposal.id}", sender, chat))
        session.flush()

    @patch.object(main, "safe_answer_agent_callback")
    @patch.object(main, "send_telegram_text", return_value={"result": {"message_id": 71}})
    def test_handover_requires_owner_then_records_exact_target_once(self, send, answer):
        with self.Session() as session:
            _, charge = self.case(session)
            request, proposal = self.claim(session, charge)
            self.assertEqual(proposal.safety_level, 2)
            self.assertEqual(charge.personal_paid, 0)
            self.assertEqual(session.scalars(select(PaymentReceipt)).all(), [])
            self.assertIn("2 000", proposal.preview_text.replace("\xa0", " "))
            main.handle_cash_payment_callback(session, self.callback(f"cashpay:{request.token}"))
            self.assertEqual(len(session.scalars(select(AgentActionProposal)).all()), 1)
            self.approve(session, proposal, sender="501")
            self.approve(session, proposal, chat="901")
            self.assertEqual(charge.personal_paid, 0)
            self.approve(session, proposal)
            self.approve(session, proposal)
            receipts = session.scalars(select(PaymentReceipt)).all()
            self.assertEqual(len(receipts), 1)
            self.assertEqual((receipts[0].source, receipts[0].channel, receipts[0].rent_charge_id), ("cash_confirmed", "personal", charge.id))
            self.assertEqual((charge.ip_paid, charge.personal_paid), (0, 2000))
            self.assertEqual(request.status, "accepted")
            self.assertIn("наличные", main.payment_source_label(receipts[0]))

    @patch.object(main, "safe_answer_agent_callback")
    @patch.object(main, "send_telegram_text", return_value={})
    def test_both_parts_cash_are_applied_to_their_channels(self, send, answer):
        with self.Session() as session:
            _, charge = self.case(session, both=True)
            request, proposal = self.claim(session, charge)
            main.decide_hermes_proposal(session, proposal.id, decision="confirm", actor="test-owner")
            self.assertEqual((charge.ip_paid, charge.personal_paid, charge.status), (10000, 2000, "paid"))
            self.assertEqual(len(session.scalars(select(PaymentReceipt)).all()), 2)
            self.assertEqual(request.status, "accepted")

    @patch.object(main, "safe_answer_agent_callback")
    @patch.object(main, "send_telegram_text", return_value={})
    def test_changed_amount_or_payment_method_cannot_be_confirmed(self, send, answer):
        for change in ("amount", "method", "archive", "link"):
            with self.subTest(change=change), self.Session() as session:
                _, charge = self.case(session)
                request, proposal = self.claim(session, charge)
                if change == "amount":
                    charge.personal_due = 3000
                elif change == "method":
                    charge.lease.apartment.object.payment_profile.personal_payment_method = "transfer"
                elif change == "archive":
                    main.set_lease_ignored(session, charge.lease_id, True)
                else:
                    session.get(AppSetting, "telegram_tenant_links").value = '{}'
                session.flush()
                self.approve(session, proposal)
                self.assertEqual(proposal.status, "failed")
                self.assertEqual(session.scalars(select(PaymentReceipt)).all(), [])
                session.rollback()

    @patch.object(main, "safe_answer_agent_callback")
    @patch.object(main, "send_telegram_text", return_value={})
    def test_tenant_identity_expiry_rejection_and_missing_owner(self, send, answer):
        with self.Session() as session:
            _, charge = self.case(session)
            request = offer_cash_payment(session, charge, "501")
            for sender, chat in (("502", "501"), ("501", "502")):
                main.handle_cash_payment_callback(session, self.callback(f"cashpay:{request.token}", sender, chat))
            self.assertEqual(request.status, "offered")
            request, proposal = self.claim(session, charge)
            proposal.expires_at = main.utc_now() - timedelta(seconds=1)
            self.approve(session, proposal)
            self.assertEqual(proposal.status, "expired")
            new_request = offer_cash_payment(session, charge, "501")
            self.assertNotEqual(new_request.id, request.id)
            request, proposal = self.claim(session, charge)
            main.handle_agent_callback_query(session, self.callback(f"agent:reject:{proposal.id}", "900", "900"))
            self.assertEqual(proposal.status, "rejected")
            self.assertEqual(session.scalars(select(PaymentReceipt)).all(), [])
            offer_cash_payment(session, charge, "501")
            session.get(AppSetting, "telegram_owner_chat_id").value = ""
            session.flush()
            new_request = offer_cash_payment(session, charge, "501")
            main.handle_cash_payment_callback(session, self.callback(f"cashpay:{new_request.token}"))
            self.assertEqual(new_request.status, "offered")

    @patch.object(main, "safe_answer_agent_callback")
    def test_owner_delivery_failure_can_be_retried_without_losing_claim(self, answer):
        with self.Session() as session:
            _, charge = self.case(session)
            request = offer_cash_payment(session, charge, "501")
            session.commit()
            with patch.object(main, "send_telegram_text", side_effect=HTTPException(502, "offline")):
                main.handle_cash_payment_callback(session, self.callback(f"cashpay:{request.token}"))
            session.refresh(request)
            self.assertEqual(request.status, "offered")
            self.assertEqual(session.scalars(select(AgentActionProposal)).all(), [])
            with patch.object(main, "send_telegram_text", return_value={}):
                self.claim(session, charge)

    @patch.object(main, "send_telegram_text", return_value={})
    def test_three_day_and_due_reminders_use_cash_button_and_deduplicate(self, send):
        today = date.today()
        with self.Session() as session:
            lease, charge = self.case(session, both=True, due_date=today + timedelta(days=3))
            with patch.object(main, "local_now", return_value=datetime.combine(today, datetime.min.time(), tzinfo=main.LOCAL_TZ).replace(hour=12)):
                main.run_due_reminders(session, today)
                main.run_due_reminders(session, today)
            notices = [call for call in send.call_args_list if str(call.args[1]) == "501"]
            self.assertEqual(len(notices), 1)
            self.assertIn("заранее снимите наличные", notices[0].args[2])
            buttons = notices[0].args[3]["inline_keyboard"]
            self.assertEqual(buttons[0][0]["text"], "Деньги переданы")
            self.assertFalse(any(button["callback_data"].endswith(":receipt") for row in buttons for button in row))
            text = main.render_message_text(session, "message_rent_due", lease, charge)
            self.assertIn("наличными", text)
            self.assertNotIn("номер не указан", text)
            self.assertNotIn("только на расчётный счёт", main.tenant_requisites_text(session, lease))

    @patch.object(main, "safe_answer_agent_callback")
    @patch.object(main, "send_telegram_text", return_value={})
    def test_parallel_requests_and_other_payment_do_not_double_credit(self, send, answer):
        with self.Session() as session:
            _, charge = self.case(session)
            request, first = self.claim(session, charge)
            duplicate = CashPaymentRequest(
                token="second-offer", rent_charge_id=charge.id, tenant_chat_id="501",
                ip_amount=request.ip_amount, personal_amount=request.personal_amount,
                snapshot_json=request.snapshot_json,
            )
            session.add(duplicate)
            session.flush()
            main.handle_cash_payment_callback(session, self.callback(f"cashpay:{duplicate.token}"))
            second = session.get(AgentActionProposal, duplicate.proposal_id)
            self.approve(session, first)
            self.approve(session, second)
            self.assertEqual(second.status, "failed")
            self.assertEqual(len(session.scalars(select(PaymentReceipt)).all()), 1)
            self.assertEqual(charge.personal_paid, 2000)

    @patch.object(main, "send_telegram_text", return_value={})
    def test_cash_reminder_is_sent_on_due_day_but_not_four_days_early(self, send):
        today = date.today()
        with self.Session() as session:
            _, charge = self.case(session, due_date=today + timedelta(days=4))
            with patch.object(main, "local_now", return_value=datetime.combine(today, datetime.min.time(), tzinfo=main.LOCAL_TZ).replace(hour=12)):
                main.run_due_reminders(session, today)
            self.assertFalse(any(str(call.args[1]) == "501" for call in send.call_args_list))
            charge.due_date = today
            session.flush()
            with patch.object(main, "local_now", return_value=datetime.combine(today, datetime.min.time(), tzinfo=main.LOCAL_TZ).replace(hour=12)):
                main.run_due_reminders(session, today)
            notice = next(call for call in send.call_args_list if str(call.args[1]) == "501")
            self.assertIn("Напоминание об оплате аренды", notice.args[2])
            self.assertEqual(notice.args[3]["inline_keyboard"][0][0]["text"], "Деньги переданы")
            self.assertTrue(any(button["callback_data"].endswith(":receipt") for row in notice.args[3]["inline_keyboard"] for button in row))

    def test_profile_validation_and_default_transfer(self):
        with self.Session() as session:
            with self.assertRaises(HTTPException):
                main.create_payment_profile({"name": "bad", "ip_payment_method": "other"}, session)
            profile = main.create_payment_profile({"name": "old-compatible"}, session)
            self.assertEqual(profile["ip_payment_method"], "transfer")
            self.assertEqual(profile["personal_payment_method"], "transfer")
        decision = ACTION_SAFETY_REGISTRY.classify("confirm_cash_payment", owner_level_one_enabled=True)
        self.assertTrue(decision.confirmation_required)
        self.assertFalse(decision.autonomous_allowed)


if __name__ == "__main__":
    unittest.main()
