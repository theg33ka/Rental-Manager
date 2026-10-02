from __future__ import annotations

from datetime import date, datetime, timedelta
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import asyncio
import urllib.error

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker

from rental_manager.database import Base
from rental_manager import models as m
from rental_manager.main import situation_debt, manual_debt_debt, api_manager_chat
from rental_manager.services.deepseek_client import DeepSeekClient, DeepSeekResult
from rental_manager.services.telegram_bot import TelegramApiError, telegram_api_request
from rental_manager.security.sessions import issue_session
from rental_manager.services.ai_providers import AiProvider, build_provider_runtime, provider_chain
from rental_manager.services.hermes.agent_core import run_agent
from rental_manager.services.hermes.data_tools import RentalDataTools, PROJECTIONS
from rental_manager.services.hermes.cases import reconcile_operational_cases
from rental_manager.services.hermes.memory import create_owner_commitment, is_commitment_phrase
from rental_manager.services.hermes.reminders import reminder_allowed
from rental_manager.services.hermes.notifications import (NotificationConfig, queue, quiet,
    reconcile_notifications, deliver_telegram, push_feed)


def completion(name, args):
    return DeepSeekResult("", "fake", prompt_tokens=10, completion_tokens=5,
        tool_calls=[{"id": "call_1", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}])


class ManagerAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.tmp.name).as_posix()}/test.db")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.data = RentalDataTools(self.session, owner=True, balance=situation_debt, manual_balance=manual_debt_debt)
        obj = m.RentalObject(name="Дом", short_code="Д")
        self.unit = m.Apartment(object=obj, name="7")
        self.tenant = m.Tenant(full_name="Иванов Иван")
        self.lease = m.Lease(apartment=self.unit, tenant=self.tenant, start_date=date(2026, 1, 1), payment_day=1, ip_amount=10000)
        self.charge = m.RentCharge(lease=self.lease, period_start=date(2026, 10, 1), period_end=date(2026, 10, 31),
            due_date=date(2026, 10, 1), ip_due=10000, personal_due=2000, ip_paid=3000, personal_paid=0)
        self.session.add(self.charge)
        self.session.flush()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()
        self.tmp.cleanup()

    def test_all_projections_exist_and_exclude_secrets(self):
        for name, (model, fields) in PROJECTIONS.items():
            self.data.query(resource=name)
            for field in fields.split():
                self.assertTrue(hasattr(model, field))
                self.assertNotIn(field, {"token", "chat_id", "password_hash", "file_path", "recipient_details", "metadata_json"})

    def test_current_partial_debt_and_overpayment_channels(self):
        self.assertEqual(self.data.debt(self.lease.id)["total_debt"], 9000)
        self.charge.ip_paid = 15000
        self.session.flush()
        self.assertEqual(self.data.debt(self.lease.id)["total_debt"], 2000)
        self.charge.personal_paid = 3000
        self.session.flush()
        self.assertEqual(self.data.debt(self.lease.id)["total_debt"], 0)

    def test_utility_manual_and_old_closed_debt(self):
        service = m.UtilityService(object=self.unit.object, name="Свет", kind="electricity")
        bill = m.UtilityBill(service=service, period_start=date(2026, 9, 1), period_end=date(2026, 9, 30), status="issued")
        bill.lines.append(m.UtilityBillLine(apartment=self.unit, lease=self.lease, total_amount=4200, paid_amount=200, status="issued"))
        bill.lines.append(m.UtilityBillLine(apartment=self.unit, lease=self.lease, total_amount=90000, status="draft"))
        manual = m.ManualDebt(lease=self.lease, apartment=self.unit, amount=500, paid_amount=100, active=True)
        old = m.RentCharge(lease=self.lease, period_start=date(2026, 8, 1), period_end=date(2026, 8, 31), due_date=date(2026, 8, 1), ip_due=10000, ip_paid=10000)
        self.session.add_all([bill, manual, old])
        self.session.flush()
        debt = self.data.debt(self.lease.id)
        self.assertEqual(debt["total_debt"], 13400)
        self.assertEqual(debt["rent"]["records_checked"], 2)

    def test_occupancy_history_never_merges_tenants(self):
        second = m.Tenant(full_name="Иванов Пётр")
        another_unit = m.Apartment(object=self.unit.object, name="8")
        leases = [m.Lease(apartment=self.unit, tenant=second, start_date=date(2025, 1, 1), end_date=date(2025, 12, 31), payment_day=1, active=False),
                  m.Lease(apartment=another_unit, tenant=self.tenant, start_date=date(2024, 1, 1), end_date=date(2024, 12, 31), payment_day=1, active=False)]
        self.session.add_all(leases)
        self.session.flush()
        self.assertEqual(len(self.data.query(resource="tenants", search="иванов")["records"]), 2)
        rows = self.data.query(resource="leases", filters={"tenant_id": self.tenant.id})["records"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({row["apartment_id"] for row in rows}), 2)
        period = self.data.query(resource="leases", filters={"apartment_id": self.unit.id, "start_date__gte": "2026-01-01"})["records"]
        self.assertEqual([row["id"] for row in period], [self.lease.id])

    def test_query_pagination_and_injection(self):
        self.session.add(m.Tenant(full_name="Петров"))
        self.session.flush()
        first = self.data.query(resource="tenants", limit=1)
        self.assertTrue(first["has_more"])
        second = self.data.query(resource="tenants", limit=1, after_id=first["next_after_id"])
        self.assertFalse(second["has_more"])
        for args in [{"resource": "app_settings"}, {"resource": "tenants", "filters": {"id; DROP TABLE tenants": 1}},
                     {"resource": "tenants", "limit": 10000}, {"resource": "tenants", "sql": "SELECT 1"}]:
            with self.assertRaises(ValueError):
                self.data.query(**args)
        self.assertEqual(self.data.query(resource="tenants", search="' OR 1=1 --")["records"], [])

    def test_owner_authorization(self):
        with self.assertRaises(PermissionError):
            RentalDataTools(self.session, owner=False, balance=situation_debt, manual_balance=manual_debt_debt)

    def test_backend_analytics_sums_filtered_rows(self):
        result = self.data.aggregate(resource="charges", filters={"lease_id": self.lease.id},
            sum_fields=["ip_due", "ip_paid"], group_by="lease_id")
        self.assertEqual(result["groups"][str(self.lease.id)], {"count": 1, "ip_due": 10000, "ip_paid": 3000})
        with self.assertRaises(ValueError):
            self.data.aggregate(resource="charges", sum_fields=["status"])

    def test_archive_never_generates_owner_notifications(self):
        reconcile_notifications(self.session, now=datetime(2026, 10, 5, 10), excluded_leases={self.lease.id})
        self.assertEqual(self.session.scalar(select(func.count(m.AgentNotification.id))), 0)

    def test_owner_chat_uses_native_tools_and_counts_every_call(self):
        from rental_manager import main
        self.session.add(m.AppSetting(key="ai_enabled", value="true"))
        self.session.flush()
        replies = [completion("get_debt_state", {"lease_id": self.lease.id}),
            completion("finish_answer", {"reply": "Остаток 9 000 ₽", "evidence": [1]})]
        from types import SimpleNamespace
        client = SimpleNamespace(chat_completions=lambda **kwargs: replies.pop(0))
        runtime = SimpleNamespace(model="deepseek-v4-flash", provider=AiProvider.DEEPSEEK, client=client)
        with patch.object(main, "build_provider_runtime", return_value=runtime):
            response = api_manager_chat({"text": "Сколько осталось по договору 1?"}, self.session)
        self.assertIn("9 000", response["reply"])
        self.assertEqual(self.session.scalar(select(func.sum(m.AiUsageDaily.calls))), 2)
        self.assertEqual(self.session.scalar(select(func.count(m.AiMessage.id))), 2)

    def test_five_plus_tool_calls_and_second_read(self):
        replies = [completion("catalog", {})]
        replies += [completion("query_records", {"resource": name}) for name in ["tenants", "leases", "charges", "payments", "utility_lines", "adjustments"]]
        replies += [completion("get_debt_state", {"lease_id": self.lease.id}),
                    completion("finish_answer", {"reply": "Остаток 9 000 ₽.", "evidence": [2, 3, 4, 5, 6, 7, 8]})]
        result = run_agent(data=self.data, messages=[], complete=lambda **kw: replies.pop(0))
        self.assertEqual(result.llm_calls, 9)
        self.assertEqual(len(result.calls), 8)
        self.assertIn("Проверено", result.envelope.reply)

    def test_unread_page_cannot_claim_complete(self):
        self.session.add(m.Tenant(full_name="Ещё жилец"))
        self.session.flush()
        replies = [completion("query_records", {"resource": "tenants", "limit": 1}),
                   completion("finish_answer", {"reply": "Найден жилец", "evidence": [1]})]
        result = run_agent(data=self.data, messages=[], complete=lambda **kw: replies.pop(0))
        self.assertEqual(result.stop_reason, "partial")
        self.assertIn("не дочитана", result.envelope.reply)

    def test_no_evidence_cannot_invent_debt(self):
        result = run_agent(data=self.data, messages=[], complete=lambda **kw: completion("finish_answer", {"reply": "Долг 999999 ₽", "evidence": [33]}), max_steps=3)
        self.assertNotIn("999999", result.envelope.reply)
        self.assertEqual(result.stop_reason, "repeated_call")

    def test_unbacked_amount_rejected_even_with_real_entity_evidence(self):
        replies = [completion("query_records", {"resource": "tenants"}),
            completion("finish_answer", {"reply": "Долг 999999 ₽", "evidence": [1]}),
            completion("get_debt_state", {"lease_id": self.lease.id}),
            completion("finish_answer", {"reply": "Остаток 9000 ₽", "evidence": [3]})]
        result = run_agent(data=self.data, messages=[], complete=lambda **kw: replies.pop(0))
        self.assertNotIn("999999", result.envelope.reply)
        self.assertIn("9000", result.envelope.reply)

    def test_event_history_exposes_financial_snapshot_without_secret_payload(self):
        event = m.DomainEvent(event_type="charge_changed", idempotency_key="test-change", entity_type="RentCharge",
            entity_id=str(self.charge.id), payload_json=json.dumps({"ip_due": 20000, "password_hash": "secret", "token": "secret"}))
        self.session.add(event)
        self.session.flush()
        row = self.data.query(resource="events")["records"][0]
        self.assertEqual(row["snapshot"], {"ip_due": 20000})
        self.assertNotIn("secret", json.dumps(row))

    def test_provider_down_tool_down_and_limits(self):
        result = run_agent(data=self.data, messages=[], complete=lambda **kw: None)
        self.assertEqual(result.stop_reason, "provider_unavailable_or_budget")
        self.assertNotIn("0 ₽", result.envelope.reply)
        with patch.object(self.data, "execute", side_effect=RuntimeError("secret")):
            result = run_agent(data=self.data, messages=[], complete=lambda **kw: completion("query_records", {"resource": "leases"}), max_steps=2)
        self.assertNotIn("secret", json.dumps(result.calls))
        self.assertEqual(result.stop_reason, "step_limit")
        result = run_agent(data=self.data, messages=[], complete=lambda **kw: None, timeout_seconds=0)
        self.assertEqual(result.stop_reason, "timeout")

    def test_native_provider_tools_and_secret_safe_error(self):
        client = DeepSeekClient("https://example.test", "test")
        payload = {"choices": [{"message": {"content": None, "tool_calls": [{"id": "a"}]}}]}
        with patch.object(client, "_post_json", return_value=payload) as send:
            result = client.chat_completions(model="fake", messages=[], tools=[{"type": "function"}], timeout_seconds=3)
        self.assertEqual(result.tool_calls, [{"id": "a"}])
        self.assertIn("tools", send.call_args.args[1])

    def test_switch_provider(self):
        env = {"RENTAL_AI_PROVIDER": "compatible", "RENTAL_AI_BASE_URL": "https://example.test/v1", "RENTAL_AI_API_KEY": "test", "RENTAL_AI_MODEL": "another-model",
            "RENTAL_AI_INPUT_RUB_PER_MILLION": "100", "RENTAL_AI_OUTPUT_RUB_PER_MILLION": "400"}
        self.assertEqual(provider_chain(env), [AiProvider.COMPATIBLE])
        runtime = build_provider_runtime(AiProvider.COMPATIBLE, requested_model="unused", environ=env)
        self.assertEqual(runtime.model, "another-model")
        from rental_manager.main import ai_estimated_cost_rub
        with patch.dict("os.environ", env):
            self.assertEqual(ai_estimated_cost_rub("another-model", 1000000, 2000000), 900)
        del env["RENTAL_AI_INPUT_RUB_PER_MILLION"]
        with self.assertRaises(ValueError):
            build_provider_runtime(AiProvider.COMPATIBLE, requested_model="unused", environ=env)

    def test_full_text_read_is_bounded_and_secrets_stay_unavailable(self):
        original = "Сохранённая история " * 401
        row = m.MessageLog(lease_id=self.lease.id, text=original, status="sent", channel="telegram")
        self.session.add(row)
        self.session.flush()
        record = self.data.query(resource="messages")["records"][0]
        self.assertEqual(record["truncated_fields"], ["text"])
        pieces = []
        offset = 0
        while True:
            result = self.data.read_text("messages", row.id, "text", offset)
            self.assertLessEqual(len(result["text"]), 3000)
            pieces.append(result["text"])
            if not result["has_more"]:
                break
            offset = result["next_offset"]
        self.assertEqual("".join(pieces), original)
        with self.assertRaises(ValueError):
            self.data.read_text("payments", 1, "recipient_details")

    def test_pause_until_monday_reconcile_then_follow_up(self):
        friday = datetime(2026, 10, 2, 11)
        with patch("rental_manager.services.hermes.cases.utc_now", return_value=friday):
            case = reconcile_operational_cases(self.session, friday.date())[0]
            self.assertTrue(is_commitment_phrase("До понедельника не трогай"))
            commitment = create_owner_commitment(self.session, case=case, text="До понедельника не трогай", now=friday)
            self.session.flush()
            reconcile_operational_cases(self.session, friday.date())
        self.assertEqual(commitment.due_at, datetime(2026, 10, 5, 10))
        self.assertEqual(case.suppression_until, commitment.due_at)
        situation = m.PaymentSituation(lease_id=self.lease.id, kind="rent", reference_id=self.charge.id)
        self.session.add(situation)
        self.session.flush()
        self.assertEqual(reminder_allowed(self.session, situation=situation, now=friday)[1], "owner_requested_pause")
        reconcile_notifications(self.session, now=friday)
        self.assertEqual(self.session.scalar(select(func.count(m.AgentNotification.id))), 0)
        monday = datetime(2026, 10, 5, 11)
        with patch("rental_manager.services.hermes.cases.utc_now", return_value=monday):
            reconcile_notifications(self.session, now=monday)
        notification = self.session.scalar(select(m.AgentNotification).where(m.AgentNotification.case_id == case.id))
        self.assertIn("Возвращаемся", notification.text)
        count = self.session.scalar(select(func.count(m.AgentNotification.id)))
        reconcile_notifications(self.session, now=monday)
        self.assertEqual(self.session.scalar(select(func.count(m.AgentNotification.id))), count)

    def test_paid_before_review_closes_case_and_commitment(self):
        now = datetime(2026, 10, 2, 10)
        case = reconcile_operational_cases(self.session, now.date())[0]
        commitment = create_owner_commitment(self.session, case=case, text="До понедельника не трогай", now=now)
        self.charge.ip_paid = self.charge.ip_due
        self.charge.personal_paid = self.charge.personal_due
        self.session.flush()
        reconcile_notifications(self.session, now=now)
        self.assertEqual(case.status, "resolved")
        self.assertEqual(commitment.status, "completed")
        self.assertEqual(self.session.scalar(select(func.count(m.AgentNotification.id))), 0)

    def test_delivery_failure_is_not_read_or_blindly_retried(self):
        cfg = NotificationConfig(quiet_start=0, quiet_end=0)
        row = queue(self.session, key="sample", text="Проверка", level="critical", cfg=cfg)[0]
        def failing(text):
            raise TimeoutError("private error")
        deliver_telegram(self.session, failing)
        self.assertEqual(row.status, "uncertain")
        self.assertIsNone(row.read_at)
        self.assertIsNone(row.delivered_at)
        deliver_telegram(self.session, lambda text: self.fail("Must not repeat uncertain send"))

    def test_delivery_success_and_deduplication(self):
        cfg = NotificationConfig()
        row = queue(self.session, key="sample", text="Проверка", level="critical", cfg=cfg)[0]
        again = queue(self.session, key="sample", text="Проверка", level="critical", cfg=cfg)[0]
        self.assertEqual(row.id, again.id)
        deliver_telegram(self.session, lambda text: {"ok": True, "result": {"message_id": 17}})
        self.assertEqual(row.status, "sent")
        self.assertEqual(row.remote_message_id, "17")
        self.assertIsNone(row.delivered_at)

    def test_push_offline_stays_pending_and_quiet_hours(self):
        cfg = NotificationConfig(mode="push")
        row = queue(self.session, key="sample", text="Проверка", level="attention", cfg=cfg)[0]
        self.assertTrue(quiet(cfg, datetime(2026, 10, 2, 16)))
        self.assertEqual(push_feed(self.session, now=datetime(2026, 10, 2, 16)), [])
        self.assertEqual(row.status, "pending")
        self.assertEqual(len(push_feed(self.session, now=datetime(2026, 10, 2, 5))), 1)

    def test_daily_summary_does_not_repeat_old_debt_or_create_new_case(self):
        self.charge.due_date = date(2026, 1, 1)
        self.session.add(m.Expense(expense_date=date(2026, 1, 1), category="Старый расход", amount=100001,
            source_funds="personal", compensation_status="pending"))
        self.session.flush()
        now = datetime(2026, 10, 2, 13)
        reconcile_notifications(self.session, now=now)
        first_cases = self.session.scalar(select(func.count(m.OperationalCase.id)))
        reconcile_notifications(self.session, now=now + timedelta(days=1))
        self.assertEqual(self.session.scalar(select(func.count(m.OperationalCase.id))), first_cases)
        rows = self.session.scalars(select(m.AgentNotification)).all()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row.dedupe_key.startswith("daily:") for row in rows))
        self.assertTrue(all("9000" not in row.text for row in rows))

    def test_panel_commitment_uses_explicit_case_and_audits_message(self):
        case = reconcile_operational_cases(self.session, date(2026, 10, 2))[0]
        response = api_manager_chat({"text": "До понедельника не трогай", "case_id": case.id}, self.session)
        self.assertIn("приостановлены", response["reply"])
        self.assertEqual(self.session.scalar(select(func.count(m.AiMessage.id))), 2)

    def test_payment_delay_reconciles_both_channels_and_old_unknown(self):
        self.charge.ip_paid = 10000
        self.charge.personal_paid = 2000
        self.session.add_all([
            m.PaymentReceipt(rent_charge_id=self.charge.id, amount=6000, channel="ip", paid_at=datetime(2026, 10, 1)),
            m.PaymentReceipt(rent_charge_id=self.charge.id, amount=4000, channel="ip", paid_at=datetime(2026, 10, 3)),
            m.PaymentReceipt(rent_charge_id=self.charge.id, amount=2000, channel="personal", paid_at=datetime(2026, 10, 6)),
            m.RentCharge(lease=self.lease, period_start=date(2026, 9, 1), period_end=date(2026, 9, 30),
                due_date=date(2026, 9, 1), ip_due=10000, ip_paid=10000)])
        self.session.flush()
        result = self.data.payment_timing(start="2026-09-01", end="2026-10-31")
        group = result["tenants"][0]
        self.assertEqual((group["known_paid"], group["paid_late"], group["unknown"]), (1, 1, 1))
        self.assertEqual(group["charges"][0]["paid_on"], "2026-10-06")
        self.charge.ip_paid = 9000
        self.session.flush()
        with patch("rental_manager.models.utc_now", return_value=datetime(2026, 10, 10)):
            result = self.data.payment_timing(start="2026-09-01", end="2026-10-31")
        self.assertEqual(result["tenants"][0]["currently_late"], 1)

    def test_payment_delay_refunds_are_unknown_and_deferral_is_explicit(self):
        self.charge.ip_paid = 10000
        self.charge.personal_due = 0
        self.charge.deferral_until = date(2026, 10, 7)
        self.session.add(m.PaymentReceipt(rent_charge_id=self.charge.id, amount=10000, channel="ip", paid_at=datetime(2026, 10, 6)))
        self.session.flush()
        self.assertEqual(self.data.payment_timing(start="2026-10-01", end="2026-10-31")["tenants"][0]["paid_late"], 1)
        self.assertEqual(self.data.payment_timing(start="2026-10-01", end="2026-10-31", original_due=False)["tenants"][0]["paid_late"], 0)
        self.session.add_all([m.PaymentReceipt(rent_charge_id=self.charge.id, amount=-100, channel="ip"),
            m.PaymentReceipt(rent_charge_id=self.charge.id, amount=100, channel="ip")])
        self.session.flush()
        self.assertEqual(self.data.payment_timing(start="2026-10-01", end="2026-10-31")["tenants"][0]["unknown"], 1)

    def test_delivery_rate_limit_retries_only_after_delay(self):
        row = queue(self.session, key="limited", text="Проверка", level="critical", cfg=NotificationConfig())[0]
        now = datetime(2026, 10, 2, 5)
        def limited(text):
            raise TelegramApiError("limit", status_code=429, retry_after=120)
        deliver_telegram(self.session, limited, now=now)
        self.assertEqual(row.status, "failed")
        deliver_telegram(self.session, lambda text: self.fail("Early retry"), now=now + timedelta(seconds=30))
        deliver_telegram(self.session, lambda text: {"ok": True, "result": {"message_id": 42}}, now=now + timedelta(minutes=3))
        self.assertEqual(row.status, "sent")
        self.assertEqual(row.attempts, 2)

    def test_interrupted_delivery_requires_explicit_checked_retry(self):
        from rental_manager.main import api_manager_retry_notification
        row = queue(self.session, key="interrupted", text="Проверка", level="critical", cfg=NotificationConfig())[0]
        row.status = "sending"
        row.next_attempt_at = datetime(2026, 10, 1)
        self.session.commit()
        deliver_telegram(self.session, lambda text: self.fail("Interrupted send must not repeat"))
        self.assertEqual(row.status, "uncertain")
        with self.assertRaises(HTTPException):
            api_manager_retry_notification(row.id, {}, self.session)
        api_manager_retry_notification(row.id, {"checked_telegram": True}, self.session)
        self.assertEqual(row.status, "pending")
        self.assertIsNotNone(self.session.scalar(select(m.DomainEvent).where(m.DomainEvent.event_type == "manager_notification_retry_requested")))

    def test_telegram_no_network_retry_when_delivery_is_uncertain(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")) as transport:
            with self.assertRaises(TelegramApiError):
                telegram_api_request("synthetic", "sendMessage", {"chat_id": "test", "text": "test"}, retry_network=False)
        self.assertEqual(transport.call_count, 1)

    def test_manager_routes_require_owner_and_csrf(self):
        from rental_manager import main
        factory = sessionmaker(bind=self.engine)
        paths = [("GET", "/api/hermes/chat"), ("POST", "/api/hermes/chat"),
            ("PUT", "/api/hermes/notification-settings"), ("POST", "/api/hermes/notifications/1/retry"),
            ("POST", "/api/hermes/notifications/1/delivered")]
        self.session.commit()
        async def next_handler(request):
            return JSONResponse({"ok": True})
        for role in (None, "guest", "owner"):
            with factory() as session:
                issued = issue_session(session, role) if role else None
            for method, path in paths:
                for valid_csrf in (False, True):
                    headers = [] if not issued else [(b"cookie", f"rental_manager_panel_session={issued.token}".encode())]
                    if issued and valid_csrf:
                        headers.append((b"x-csrf-token", issued.csrf_token.encode()))
                    request = Request({"type": "http", "method": method, "path": path, "headers": headers,
                        "query_string": b"", "server": ("test", 80), "client": ("127.0.0.1", 123), "scheme": "http"})
                    with patch.object(main, "SessionLocal", factory):
                        result = asyncio.run(main.panel_auth_middleware(request, next_handler))
                    expected = 401 if role is None else 403 if role == "guest" or (method != "GET" and not valid_csrf) else 200
                    self.assertEqual(result.status_code, expected, (role, method, valid_csrf))


if __name__ == "__main__":
    unittest.main()
