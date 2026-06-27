from __future__ import annotations

import tempfile
import unittest
import urllib.error
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from rental_manager.database import Base
from rental_manager.main import (
    ai_budget_exceeded,
    ai_estimated_cost_rub,
    app_base_url,
    call_hermes_ai,
    handle_telegram_message,
    hermes_client_for_settings,
    increment_ai_usage_row,
    process_telegram_update_background,
    resolve_ai_model,
    telegram_owner_chat_id,
    telegram_secret,
    telegram_set_webhook,
    telegram_token,
    telegram_webhook_info,
)
from rental_manager.models import AiUsageDaily, Apartment, AppSetting, Lease, RentalObject, RentCharge, Tenant
from rental_manager.services.ai_context import tenant_context_text
from rental_manager.services.ai_policy import AI_UNAVAILABLE_TEXT, TENANT_SYSTEM_PROMPT
from rental_manager.services.hermes_client import HermesClient, HermesClientError, HermesResult, YandexOpenAIClient
from rental_manager.services.telegram_bot import TelegramApiError, telegram_api_request


class AiAgentDatabaseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        db_path = Path(self.tmp.name) / "test.db"
        self.engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, future=True)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.tmp.cleanup()

    def create_two_leases(self, session):
        rental_object = RentalObject(name="House")
        apartments = [Apartment(object=rental_object, name="A1"), Apartment(object=rental_object, name="A2")]
        tenants = [Tenant(full_name="Tenant One"), Tenant(full_name="Tenant Two")]
        session.add_all([rental_object, *apartments, *tenants])
        session.flush()
        leases = [
            Lease(apartment=apartments[0], tenant=tenants[0], start_date=date(2026, 5, 1), payment_day=1, ip_amount=10000, personal_amount=2000),
            Lease(apartment=apartments[1], tenant=tenants[1], start_date=date(2026, 5, 1), payment_day=1, ip_amount=30000, personal_amount=4000),
        ]
        session.add_all(leases)
        session.flush()
        session.add(RentCharge(lease=leases[0], period_start=date(2026, 5, 1), period_end=date(2026, 5, 31), due_date=date(2026, 5, 1), ip_due=10000, personal_due=2000))
        session.add(RentCharge(lease=leases[1], period_start=date(2026, 5, 1), period_end=date(2026, 5, 31), due_date=date(2026, 5, 1), ip_due=30000, personal_due=4000))
        session.flush()
        return leases


class AiContextTests(AiAgentDatabaseTestCase):
    def test_tenant_context_does_not_include_other_tenant(self) -> None:
        with self.Session() as session:
            leases = self.create_two_leases(session)

            context = tenant_context_text(session, leases[0], {}, today=date(2026, 5, 2))

        self.assertIn("Tenant One", context)
        self.assertIn("A1", context)
        self.assertNotIn("Tenant Two", context)
        self.assertNotIn("A2", context)
        self.assertNotIn("34 000", context)


class AiBudgetTests(AiAgentDatabaseTestCase):
    def test_budget_cap_blocks_ai_calls(self) -> None:
        with self.Session() as session:
            session.add(AppSetting(key="ai_monthly_budget_rub", value="1000"))
            session.add(AiUsageDaily(usage_date=date.today(), provider="hermes", model="yandexgpt-lite", cost_rub=1000, calls=1))
            session.flush()

            self.assertTrue(ai_budget_exceeded(session))

    def test_add_ai_usage_treats_null_counters_as_zero(self) -> None:
        usage = AiUsageDaily(usage_date=date.today(), provider="hermes", model="yandexgpt-lite")
        usage.prompt_tokens = None
        usage.completion_tokens = None
        usage.total_tokens = None
        usage.cost_rub = None
        usage.calls = None

        increment_ai_usage_row(
            usage,
            HermesResult(content="ok", model="yandexgpt-lite", prompt_tokens=10, completion_tokens=5),
            0.12,
        )

        self.assertEqual(usage.prompt_tokens, 10)
        self.assertEqual(usage.completion_tokens, 5)
        self.assertEqual(usage.total_tokens, 15)
        self.assertEqual(usage.cost_rub, 0.12)
        self.assertEqual(usage.calls, 1)


class AiModelTests(unittest.TestCase):
    def test_yandex_model_alias_includes_folder_id(self) -> None:
        with patch.dict("os.environ", {"YANDEX_FOLDER_ID": "folder-123"}, clear=False):
            self.assertEqual(resolve_ai_model("yandexgpt-lite"), "gpt://folder-123/yandexgpt-lite/latest")

    def test_yandex_cost_uses_rub_pricing(self) -> None:
        self.assertEqual(ai_estimated_cost_rub("gpt://folder-123/yandexgpt-lite/latest", 1000, 1000), 0.41)


class HermesFallbackTests(AiAgentDatabaseTestCase):
    def test_hermes_error_returns_fallback_text(self) -> None:
        with self.Session() as session:
            leases = self.create_two_leases(session)
            session.add(AppSetting(key="ai_enabled", value="1"))
            session.add(AppSetting(key="hermes_model_default", value="yandexgpt-lite"))
            session.flush()

            with patch.dict("os.environ", {"YANDEX_API_KEY": "", "AI_DIRECT_YANDEX": "0"}, clear=False), patch.object(
                HermesClient, "chat_completions", side_effect=HermesClientError("down")
            ):
                answer = call_hermes_ai(
                    session,
                    chat_id=100,
                    actor_role="tenant",
                    lease=leases[0],
                    system_prompt=TENANT_SYSTEM_PROMPT,
                    context="context",
                    user_text="Когда платить?",
                    model="yandexgpt-lite",
                )

        self.assertEqual(answer, AI_UNAVAILABLE_TEXT)

    def test_yandex_env_uses_direct_client(self) -> None:
        with self.Session() as session:
            with patch.dict(
                "os.environ",
                {
                    "YANDEX_API_KEY": "test-yandex-key",
                    "YANDEX_FOLDER_ID": "folder-123",
                    "AI_DIRECT_YANDEX": "1",
                },
                clear=False,
            ):
                client = hermes_client_for_settings(session)

        self.assertIsInstance(client, YandexOpenAIClient)


class YandexOpenAIClientTests(unittest.TestCase):
    def test_chat_completions_sends_yandex_headers(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"model":"gpt://folder-123/yandexgpt-lite/latest","choices":[{"message":{"content":"ok"}}],"usage":{"prompt_tokens":1,"completion_tokens":2}}'

        with patch("rental_manager.services.hermes_client.urllib.request.urlopen", return_value=Response()) as mocked_urlopen:
            result = YandexOpenAIClient(
                "https://ai.api.cloud.yandex.net/v1",
                "test-yandex-key",
                "folder-123",
            ).chat_completions(
                model="gpt://folder-123/yandexgpt-lite/latest",
                messages=[{"role": "user", "content": "test"}],
            )

        request = mocked_urlopen.call_args.args[0]
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(request.full_url, "https://ai.api.cloud.yandex.net/v1/chat/completions")
        self.assertEqual(headers["authorization"], "Api-Key test-yandex-key")
        self.assertEqual(headers["openai-project"], "folder-123")
        self.assertEqual(result.content, "ok")
        self.assertEqual(result.prompt_tokens, 1)
        self.assertEqual(result.completion_tokens, 2)


class HermesClientTests(unittest.TestCase):
    def test_chat_completions_sends_stable_session_header(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"model":"hermes-agent","choices":[{"message":{"content":"ok"}}]}'

        with patch("rental_manager.services.hermes_client.urllib.request.urlopen", return_value=Response()) as mocked_urlopen:
            result = HermesClient(
                "http://127.0.0.1:8642",
                "test-hermes-key",
                timeout_seconds=60,
            ).chat_completions(
                model="hermes-agent",
                messages=[{"role": "user", "content": "test"}],
                session_id="rental-manager-owner-42",
            )

        request = mocked_urlopen.call_args.args[0]
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(headers["x-hermes-session-id"], "rental-manager-owner-42")
        self.assertEqual(mocked_urlopen.call_args.kwargs["timeout"], 60)
        self.assertEqual(result.content, "ok")

    def test_chat_completions_does_not_duplicate_v1_path(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"model":"deepseek-ai/DeepSeek-V4-Pro","choices":[{"message":{"content":"ok"}}]}'

        with patch("rental_manager.services.hermes_client.urllib.request.urlopen", return_value=Response()) as mocked_urlopen:
            result = HermesClient(
                "https://inference.waw0.amvera.ru/v1",
                "test-amvera-key",
                provider_name="amvera_llm",
            ).chat_completions(
                model="deepseek-V4",
                messages=[{"role": "user", "content": "test"}],
            )

        request = mocked_urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://inference.waw0.amvera.ru/v1/chat/completions")
        self.assertEqual(result.provider, "amvera_llm")
        self.assertEqual(result.content, "ok")


class TelegramApiRequestTests(unittest.TestCase):
    def test_retries_transient_network_errors(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"ok": true, "result": {"message_id": 1}}'

        with patch("rental_manager.services.telegram_bot.time.sleep"), patch(
            "rental_manager.services.telegram_bot.urllib.request.urlopen",
            side_effect=[urllib.error.URLError(OSError(101, "Network is unreachable")), Response()],
        ) as mocked_urlopen:
            result = telegram_api_request("token", "sendMessage", {"chat_id": 1, "text": "ok"})

        self.assertTrue(result["ok"])
        self.assertEqual(mocked_urlopen.call_count, 2)


class TelegramAiRoutingTests(AiAgentDatabaseTestCase):
    def test_background_update_uses_own_session(self) -> None:
        session = MagicMock()

        class SessionContext:
            def __enter__(self):
                return session

            def __exit__(self, exc_type, exc, tb):
                return False

        message = {"chat": {"id": 999}, "from": {"id": 999}, "text": "/status"}
        with patch("rental_manager.main.SessionLocal", return_value=SessionContext()), patch(
            "rental_manager.main.handle_telegram_message"
        ) as mocked_handle:
            process_telegram_update_background({"message": message})

        mocked_handle.assert_called_once_with(session, message)
        self.assertEqual(session.commit.call_count, 2)

    def test_background_update_skips_duplicate_update_id(self) -> None:
        message = {"chat": {"id": 999}, "from": {"id": 999}, "text": "/status"}

        class SessionContext:
            def __init__(self, session):
                self.session = session

            def __enter__(self):
                return self.session

            def __exit__(self, exc_type, exc, tb):
                return False

        with self.Session() as session:
            with patch("rental_manager.main.SessionLocal", return_value=SessionContext(session)), patch(
                "rental_manager.main.handle_telegram_message"
            ) as mocked_handle:
                process_telegram_update_background({"update_id": 123, "message": message})
                process_telegram_update_background({"update_id": 123, "message": message})

        mocked_handle.assert_called_once()

    def test_telegram_settings_can_come_from_env(self) -> None:
        with self.Session() as session:
            session.add(AppSetting(key="telegram_bot_token", value="db-token"))
            session.add(AppSetting(key="telegram_owner_chat_id", value="111"))
            session.flush()

            with patch.dict(
                "os.environ",
                {
                    "APP_BASE_URL": "https://example.com/",
                    "TELEGRAM_BOT_TOKEN": "env-token",
                    "TELEGRAM_OWNER_CHAT_ID": "222",
                    "TELEGRAM_WEBHOOK_SECRET": "env-secret",
                },
                clear=False,
            ):
                self.assertEqual(app_base_url(session), "https://example.com")
                self.assertEqual(telegram_token(session), "env-token")
                self.assertEqual(telegram_owner_chat_id(session), "222")
                self.assertEqual(telegram_secret(session), "env-secret")

    def test_webhook_info_is_normalized_for_ui(self) -> None:
        with self.Session() as session:
            session.add(AppSetting(key="telegram_bot_token", value="token"))
            session.add(AppSetting(key="app_base_url", value="https://rent.example.com"))
            session.flush()

            with patch(
                "rental_manager.main.telegram_api_request",
                return_value={
                    "ok": True,
                    "result": {
                        "url": "https://rent.example.com/api/integrations/telegram/webhook",
                        "pending_update_count": 2,
                        "allowed_updates": ["message"],
                    },
                },
            ):
                result = telegram_webhook_info(session)

        self.assertTrue(result["ok"])
        self.assertEqual(result["url"], "https://rent.example.com/api/integrations/telegram/webhook")
        self.assertTrue(result["matches_expected"])
        self.assertEqual(result["pending_update_count"], 2)

    def test_set_webhook_drops_pending_updates_before_setting(self) -> None:
        with self.Session() as session:
            session.add(AppSetting(key="telegram_bot_token", value="token"))
            session.flush()

            with patch("rental_manager.main.telegram_api_request", return_value={"ok": True, "description": "ok"}) as mocked_api:
                result = telegram_set_webhook({"app_base_url": "https://rent.example.com"}, session)

        self.assertTrue(result["ok"])
        self.assertEqual(mocked_api.call_args_list[0].args, ("token", "deleteWebhook", {"drop_pending_updates": True}))
        self.assertEqual(mocked_api.call_args_list[1].args[0:2], ("token", "setWebhook"))
        self.assertTrue(mocked_api.call_args_list[1].args[2]["drop_pending_updates"])
        self.assertIn("callback_query", mocked_api.call_args_list[1].args[2]["allowed_updates"])

    def test_set_webhook_continues_when_delete_webhook_network_fails(self) -> None:
        with self.Session() as session:
            session.add(AppSetting(key="telegram_bot_token", value="token"))
            session.flush()

            with patch(
                "rental_manager.main.telegram_api_request",
                side_effect=[
                    TelegramApiError("Telegram API deleteWebhook request failed: network"),
                    {"ok": True, "description": "was set"},
                    {"ok": True},
                ],
            ) as mocked_api:
                result = telegram_set_webhook({"app_base_url": "https://rent.example.com"}, session)

        self.assertTrue(result["ok"])
        self.assertIn("deleteWebhook", result["delete_warning"])
        self.assertEqual(mocked_api.call_args_list[1].args[0:2], ("token", "setWebhook"))

    def test_status_command_does_not_call_ai(self) -> None:
        with self.Session() as session:
            session.add(AppSetting(key="telegram_owner_chat_id", value="999"))
            session.flush()
            with patch("rental_manager.main.handle_owner_ai_message") as mocked_ai, patch(
                "rental_manager.main.build_dashboard"
            ) as mocked_full_dashboard, patch(
                "rental_manager.main.build_monthly_reports"
            ) as mocked_reports, patch(
                "rental_manager.main.send_telegram_text"
            ) as mocked_send:
                handle_telegram_message(session, {"chat": {"id": 999}, "from": {"id": 999}, "text": "/status"})

        mocked_ai.assert_not_called()
        mocked_full_dashboard.assert_not_called()
        mocked_reports.assert_not_called()
        mocked_send.assert_called()

    def test_id_command_skips_tenant_lookup_and_reports(self) -> None:
        with self.Session() as session:
            session.add(AppSetting(key="telegram_owner_chat_id", value="999"))
            session.flush()
            with patch("rental_manager.main.maybe_link_tenant_chat") as mocked_link, patch(
                "rental_manager.main.build_monthly_reports"
            ) as mocked_reports, patch("rental_manager.main.send_telegram_text") as mocked_send:
                handle_telegram_message(session, {"chat": {"id": 999}, "from": {"id": 999}, "text": "/id"})

        mocked_link.assert_not_called()
        mocked_reports.assert_not_called()
        mocked_send.assert_called_once_with(session, 999, "Ваш chat id: 999")

    def test_ping_command_skips_expensive_snapshots(self) -> None:
        with self.Session() as session:
            session.add(AppSetting(key="telegram_owner_chat_id", value="999"))
            session.flush()
            with patch("rental_manager.main.build_dashboard") as mocked_dashboard, patch(
                "rental_manager.main.build_monthly_reports"
            ) as mocked_reports, patch("rental_manager.main.send_telegram_text") as mocked_send:
                handle_telegram_message(session, {"chat": {"id": 999}, "from": {"id": 999}, "text": "/ping"})

        mocked_dashboard.assert_not_called()
        mocked_reports.assert_not_called()
        self.assertIn("Pong", mocked_send.call_args.args[2])

    def test_owner_command_can_be_authorized_by_sender_id(self) -> None:
        with self.Session() as session:
            session.add(AppSetting(key="telegram_owner_chat_id", value="999"))
            session.flush()
            with patch("rental_manager.main.build_status_message", return_value="OWNER STATUS"), patch("rental_manager.main.send_telegram_text") as mocked_send:
                handle_telegram_message(
                    session,
                    {"chat": {"id": -1001, "type": "group"}, "from": {"id": 999}, "text": "/status"},
                )

        mocked_send.assert_called_with(session, -1001, "OWNER STATUS", None)


if __name__ == "__main__":
    unittest.main()
