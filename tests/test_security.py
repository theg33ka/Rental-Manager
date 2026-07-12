from __future__ import annotations

import asyncio
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.fernet import Fernet
from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request
from starlette.responses import JSONResponse

from rental_manager import main
from rental_manager.database import Base
from rental_manager.main import (
    auth_with_pin,
    current_database_snapshot,
    mark_telegram_update_seen,
    list_expenses,
    panel_auth_middleware,
    panel_role_for_pin,
    telegram_webhook,
)
from rental_manager.models import AppSetting, Expense, PanelSession
from rental_manager.security.pins import PIN_SETTING_KEYS, hash_pin, is_pin_hash, verify_pin
from rental_manager.security.secrets import ENCRYPTION_ENV, ENCRYPTION_KEY_FILE_ENV, decrypt_secret
from rental_manager.security.sessions import (
    csrf_is_valid,
    find_session,
    issue_session,
    login_fingerprint,
    login_retry_after,
    record_login_failure,
    revoke_session,
    token_hash,
)


class SecurityTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        db_path = Path(self.tmp.name) / "security.db"
        self.engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, future=True)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.tmp.cleanup()

    def test_pin_is_stored_and_verified_as_argon2id_hash(self) -> None:
        pin_hash = hash_pin("735194")
        self.assertTrue(is_pin_hash(pin_hash))
        self.assertNotIn("735194", pin_hash)
        self.assertTrue(verify_pin(pin_hash, "735194"))
        self.assertFalse(verify_pin(pin_hash, "735195"))
        with self.Session() as session:
            session.add(AppSetting(key=PIN_SETTING_KEYS["owner"], value=pin_hash))
            session.commit()
            self.assertEqual(panel_role_for_pin(session, "735194"), "owner")

    def test_session_token_is_hashed_revocable_and_csrf_bound(self) -> None:
        with self.Session() as session:
            issued = issue_session(session, "owner", "test-agent")
            self.assertIsNone(session.get(PanelSession, issued.token))
            row = session.get(PanelSession, token_hash(issued.token))
            self.assertIsNotNone(row)
            self.assertTrue(csrf_is_valid(row, issued.csrf_token))
            self.assertFalse(csrf_is_valid(row, "wrong"))
            self.assertEqual(find_session(session, issued.token).role, "owner")
            revoke_session(session, issued.token)
            self.assertIsNone(find_session(session, issued.token))

    def test_login_failures_add_retry_delay(self) -> None:
        with self.Session() as session:
            fingerprint = login_fingerprint("127.0.0.1", "test")
            self.assertEqual(login_retry_after(session, fingerprint), 0)
            delay = record_login_failure(session, fingerprint)
            self.assertGreaterEqual(delay, 1)
            self.assertGreaterEqual(login_retry_after(session, fingerprint), 1)

    def test_safe_export_excludes_secrets_and_security_tables(self) -> None:
        with self.Session() as session:
            session.add_all(
                [
                    AppSetting(key="color_palette", value="classic"),
                    AppSetting(key="telegram_bot_token", value="enc:v1:ciphertext"),
                    AppSetting(key=PIN_SETTING_KEYS["owner"], value=hash_pin("735194")),
                ]
            )
            session.commit()
            issue_session(session, "owner")
            safe = current_database_snapshot(session)
            protected = current_database_snapshot(session, include_secrets=True)
            self.assertEqual(safe["version"], 2)
            self.assertNotIn("panel_sessions", safe["tables"])
            safe_keys = {row["key"] for row in safe["tables"]["app_settings"]}
            protected_keys = {row["key"] for row in protected["tables"]["app_settings"]}
            self.assertEqual(safe_keys, {"color_palette"})
            self.assertIn("telegram_bot_token", protected_keys)
            self.assertIn(PIN_SETTING_KEYS["owner"], protected_keys)

    def test_deepseek_settings_encrypt_api_key_and_hide_its_value(self) -> None:
        encryption_key = Fernet.generate_key().decode("ascii")
        with self.Session() as session, patch.dict(
            os.environ,
            {"RENTAL_MANAGER_SETTINGS_ENCRYPTION_KEY": encryption_key},
            clear=False,
        ):
            settings = main.save_settings(
                session,
                {
                    "deepseek_api_key": "sk-test-secret",
                    "deepseek_model": "deepseek-v4-pro",
                },
            )
            stored = session.get(AppSetting, "deepseek_api_key")

        self.assertTrue(settings["deepseek_api_key_configured"])
        self.assertNotIn("deepseek_api_key", settings)
        self.assertEqual(settings["deepseek_model"], "deepseek-v4-pro")
        self.assertIsNotNone(stored)
        self.assertTrue(stored.value.startswith("enc:v1:"))
        self.assertNotIn("sk-test-secret", stored.value)

    def test_deepseek_api_key_uses_persistent_file_when_environment_key_is_missing(self) -> None:
        key_path = Path(self.tmp.name) / "settings.key"
        with self.Session() as session, patch.dict(
            os.environ,
            {
                ENCRYPTION_ENV: "",
                ENCRYPTION_KEY_FILE_ENV: str(key_path),
            },
            clear=False,
        ):
            settings = main.save_settings(session, {"deepseek_api_key": "sk-file-secret"})
            stored = session.get(AppSetting, "deepseek_api_key")
            decrypted = decrypt_secret(stored.value)

        self.assertTrue(settings["deepseek_api_key_configured"])
        self.assertTrue(key_path.is_file())
        self.assertEqual(decrypted, "sk-file-secret")
        self.assertNotIn(b"sk-file-secret", key_path.read_bytes())

    def test_unavailable_key_storage_returns_service_error_instead_of_internal_error(self) -> None:
        blocked_parent = Path(self.tmp.name) / "blocked"
        blocked_parent.write_text("not a directory", encoding="utf-8")
        with self.Session() as session, patch.dict(
            os.environ,
            {
                ENCRYPTION_ENV: "",
                ENCRYPTION_KEY_FILE_ENV: str(blocked_parent / "settings.key"),
            },
            clear=False,
        ), self.assertRaises(HTTPException) as raised:
            main.save_settings(session, {"deepseek_api_key": "sk-test-secret"})

        self.assertEqual(raised.exception.status_code, 503)

    def test_empty_environment_key_does_not_hide_saved_deepseek_key(self) -> None:
        with self.Session() as session, patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}, clear=False):
            session.add(AppSetting(key="deepseek_api_key", value="settings-key"))
            session.commit()

            self.assertEqual(main.get_setting_value(session, "deepseek_api_key"), "settings-key")

    def test_deepseek_settings_reject_unknown_model(self) -> None:
        with self.Session() as session, self.assertRaises(HTTPException) as raised:
            main.save_settings(session, {"deepseek_model": "deepseek-chat"})

        self.assertEqual(raised.exception.status_code, 400)

    def test_empty_pin_fields_do_not_block_deepseek_model_save(self) -> None:
        with self.Session() as session:
            settings = main.save_settings(
                session,
                {
                    "deepseek_model": "deepseek-v4-pro",
                    "panel_owner_pin_code": "",
                    "panel_guest_pin_code": "",
                },
            )

        self.assertEqual(settings["deepseek_model"], "deepseek-v4-pro")
        self.assertFalse(settings["panel_owner_pin_code_configured"])
        self.assertFalse(settings["panel_guest_pin_code_configured"])

    def test_telegram_update_id_is_unique(self) -> None:
        with self.Session() as session:
            self.assertTrue(mark_telegram_update_seen(session, 1001))
            session.commit()
            self.assertFalse(mark_telegram_update_seen(session, 1001))

    def test_expenses_endpoint_supports_pagination(self) -> None:
        with self.Session() as session:
            session.add_all(
                [Expense(expense_date=date(2026, 1, day), category="test", amount=float(day)) for day in range(1, 6)]
            )
            session.commit()
            page = list_expenses(limit=2, offset=1, session=session)
            self.assertEqual(len(page), 2)
            self.assertEqual([item["amount"] for item in page], [4.0, 3.0])

    def test_production_webhook_without_secret_is_rejected(self) -> None:
        request = SimpleNamespace(
            client=SimpleNamespace(host="127.0.0.1"),
            headers={},
        )
        with (
            patch.object(main, "SessionLocal", self.Session),
            patch.object(main, "production_mode", return_value=True),
            patch.dict(os.environ, {"TELEGRAM_WEBHOOK_SECRET": ""}, clear=False),
        ):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(telegram_webhook(request, {"update_id": 2002}))
        self.assertEqual(raised.exception.status_code, 503)

    def test_production_auth_cookie_has_security_flags(self) -> None:
        with self.Session() as session:
            session.add(AppSetting(key=PIN_SETTING_KEYS["owner"], value=hash_pin("735194")))
            session.commit()
            request = SimpleNamespace(
                client=SimpleNamespace(host="127.0.0.1"),
                headers={"user-agent": "test"},
            )
            response = Response()
            with patch.object(main, "production_mode", return_value=True):
                auth_with_pin(
                    {"pin_code": "735194", "remember_device": True},
                    request,
                    response,
                    session,
                )
            cookies = "\n".join(response.headers.getlist("set-cookie"))
            self.assertIn("HttpOnly", cookies)
            self.assertIn("Secure", cookies)
            self.assertIn("SameSite=lax", cookies)

    def test_state_change_requires_csrf_header(self) -> None:
        with self.Session() as session:
            issued = issue_session(session, "owner")
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/settings",
            "headers": [(b"cookie", f"rental_manager_panel_session={issued.token}".encode("ascii"))],
            "query_string": b"",
            "server": ("test", 80),
            "client": ("127.0.0.1", 1234),
            "scheme": "http",
        }

        async def call_next(_: Request) -> JSONResponse:
            return JSONResponse({"ok": True})

        with patch.object(main, "SessionLocal", self.Session):
            response = asyncio.run(panel_auth_middleware(Request(scope), call_next))
        self.assertEqual(response.status_code, 403)


class MigrationTests(unittest.TestCase):
    def test_upgrade_creates_security_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "migration.db"
            env = os.environ.copy()
            env["RENTAL_MANAGER_DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
            result = subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "20260711_01"],
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            connection = sqlite3.connect(db_path)
            try:
                connection.executemany(
                    "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
                    [
                        (PIN_SETTING_KEYS["owner"], "old-owner-hash"),
                        (PIN_SETTING_KEYS["guest"], "old-guest-hash"),
                    ],
                )
                connection.commit()
            finally:
                connection.close()
            result = subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            connection = sqlite3.connect(db_path)
            try:
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                pin_rows = dict(
                    connection.execute(
                        "SELECT key, value FROM app_settings WHERE key IN ('panel_owner_pin_hash', 'panel_guest_pin_hash')"
                    )
                )
            finally:
                connection.close()
            self.assertIn("panel_sessions", tables)
            self.assertIn("panel_login_attempts", tables)
            self.assertIn("processed_telegram_updates", tables)
            self.assertTrue(verify_pin(pin_rows[PIN_SETTING_KEYS["owner"]], "12" + "98"))
            self.assertTrue(verify_pin(pin_rows[PIN_SETTING_KEYS["guest"]], "12" + "12"))


if __name__ == "__main__":
    unittest.main()
