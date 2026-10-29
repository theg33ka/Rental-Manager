from __future__ import annotations

import asyncio
import json
import socket
import shutil
import subprocess
import threading
import time
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx
import uvicorn
from sqlalchemy import func, select

from rental_manager import main
from rental_manager.models import AiActionLog, AppSetting, Apartment, PaymentReceipt, RentalObject
from rental_manager.security.pins import PIN_SETTING_KEYS, hash_pin
from rental_manager.security.sessions import issue_session
from rental_manager.services.maintenance import apartment_finance_snapshot
from scripts.maintenance.host_client import HostClient
from tests.test_billing import DatabaseTestCase


class HostMaintenanceTests(DatabaseTestCase):
    @unittest.skipUnless(shutil.which("powershell.exe"), "Windows PowerShell is required")
    def test_windows_powershell_can_parse_credential_script(self):
        path = Path(__file__).resolve().parents[1] / "scripts" / "maintenance" / "host.ps1"
        command = "$errors=$null; [System.Management.Automation.Language.Parser]::ParseFile('" + str(path).replace("'", "''") + "', [ref]$null, [ref]$errors) | Out-Null; if ($errors) { $errors | ForEach-Object { $_.Message }; exit 1 }"
        result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def setUp(self):
        super().setUp()
        with self.Session() as session:
            apartment = Apartment(object=RentalObject(name="Дом"), name="К1")
            session.add(apartment)
            session.flush()
            self.apartment_id = apartment.id
            self.owner = issue_session(session, "owner", "maintenance-test")
            self.guest = issue_session(session, "guest", "maintenance-test")
            session.commit()

    def request(self, method, path, *, identity=None, csrf=True, payload=None):
        async def run():
            cookies = {main.PANEL_AUTH_COOKIE: identity.token} if identity else {}
            headers = {"X-CSRF-Token": identity.csrf_token} if identity and csrf else {}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                         base_url="http://test", cookies=cookies) as client:
                return await client.request(method, path, headers=headers, json=payload)

        def session_override():
            with self.Session() as session:
                yield session

        main.app.dependency_overrides[main.get_session] = session_override
        try:
            with patch.object(main, "SessionLocal", self.Session):
                return asyncio.run(run())
        finally:
            main.app.dependency_overrides.pop(main.get_session, None)

    def test_owner_only_and_csrf(self):
        backup_path = '/api/admin/maintenance/backup-status'
        self.assertEqual(self.request('GET', backup_path).status_code, 401)
        self.assertEqual(self.request('GET', backup_path, identity=self.guest).status_code, 403)
        self.assertEqual(self.request('GET', backup_path, identity=self.owner).status_code, 200)
        path = f"/api/admin/maintenance/apartments/{self.apartment_id}"
        self.assertEqual(self.request("GET", path).status_code, 401)
        self.assertEqual(self.request("GET", path, identity=self.guest).status_code, 403)
        response = self.request("GET", path, identity=self.owner)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.request("POST", "/api/admin/maintenance/proposals",
                                      identity=self.owner, csrf=False, payload={}).status_code, 403)

    def test_proposal_then_confirm_uses_common_operation_and_audit_without_telegram(self):
        plan = {"operation": "update_apartment", "arguments": {"apartment_id": self.apartment_id, "name": "К2"},
                "reason": "Исправление названия"}
        with patch.object(main, "send_telegram_text") as telegram:
            response = self.request("POST", "/api/admin/maintenance/proposals", identity=self.owner, payload=plan)
            self.assertEqual(response.status_code, 200, response.text)
            proposal = response.json()
            self.assertEqual(proposal["status"], "pending")
            with self.Session() as session:
                self.assertEqual(session.get(Apartment, self.apartment_id).name, "К1")
            duplicate = self.request("POST", "/api/admin/maintenance/proposals", identity=self.owner, payload=plan)
            self.assertEqual(duplicate.json()["id"], proposal["id"])
            confirmed = self.request("POST", f"/api/hermes/proposals/{proposal['id']}/confirm",
                                     identity=self.owner, payload={})
            self.assertEqual(confirmed.status_code, 200, confirmed.text)
            self.assertEqual(confirmed.json()["status"], "executed")
            again = self.request("POST", f"/api/hermes/proposals/{proposal['id']}/confirm",
                                 identity=self.owner, payload={})
            self.assertEqual(again.json()["status"], "executed")
            telegram.assert_not_called()
        with self.Session() as session:
            self.assertEqual(session.get(Apartment, self.apartment_id).name, "К2")
            self.assertEqual(session.scalar(select(func.count()).select_from(AiActionLog).where(
                AiActionLog.status == "executed")), 1)

    def test_transfer_repair_preview_requires_pin_and_revalidates_snapshot(self):
        from tests.test_transfer_finance_repair import TransferFinanceRepairTests

        with self.Session() as session:
            args, old, *_ = TransferFinanceRepairTests.fixture(self, session)
            session.add(AppSetting(key=PIN_SETTING_KEYS['owner'], value=hash_pin('synthetic-repair-pin')))
            session.commit()
            old_id = old.id
        args.pop('expected_fingerprint')
        with patch.object(main, 'send_telegram_text') as telegram, patch.object(main, 'configured_environment_pin_hash', return_value=''):
            response = self.request('POST', '/api/admin/maintenance/proposals', identity=self.owner,
                                    payload={'operation': 'repair_transfer_finance', 'arguments': args, 'reason': 'Synthetic repair'})
            self.assertEqual(response.status_code, 200, response.text)
            proposal = response.json()
            self.assertEqual(proposal['safety_level'], 3)
            self.assertIn('2503.05', proposal['preview'])
            with self.Session() as session:
                self.assertEqual(main.utility_advance_balance(session, session.get(main.Lease, old_id).apartment_id, old_id), 2381)
            path = f"/api/hermes/proposals/{proposal['id']}/confirm"
            self.assertEqual(self.request('POST', path, identity=self.owner, payload={}).status_code, 403)
            result = self.request('POST', path, identity=self.owner, payload={'confirmation_pin': 'synthetic-repair-pin'})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['status'], 'executed')
            telegram.assert_not_called()

    def test_unsupported_or_unexplained_operation_rejected(self):
        for plan in [{"operation": "sql", "arguments": {}, "reason": "repair"},
                     {"operation": "send_tenant_message", "arguments": {}, "reason": "repair"},
                     {"operation": "update_apartment", "arguments": {"apartment_id": self.apartment_id}}]:
            response = self.request("POST", "/api/admin/maintenance/proposals", identity=self.owner, payload=plan)
            self.assertEqual(response.status_code, 400, response.text)

    def test_snapshot_is_scoped_bounded_and_excludes_payment_credentials(self):
        with self.Session() as session:
            other = Apartment(object_id=session.get(Apartment, self.apartment_id).object_id, name="Другая")
            session.add(other)
            session.flush()
            for apartment_id, amount in [(self.apartment_id, 10), (self.apartment_id, 20), (other.id, 900)]:
                session.add(PaymentReceipt(apartment_id=apartment_id, amount=amount, channel="personal",
                                           recipient_details="PRIVATE_DETAILS", file_path="PRIVATE_PATH"))
            session.commit()
            snapshot = apartment_finance_snapshot(session, self.apartment_id, limit=1)
            self.assertTrue(snapshot["payments"]["truncated"])
            self.assertEqual(snapshot["payments"]["rows"][0]["amount"], 20)
            raw = json.dumps(snapshot, default=str)
            self.assertNotIn("PRIVATE", raw)
            self.assertNotIn("900", raw)
            self.assertFalse(session.dirty)

    def test_client_rejects_insecure_or_credential_urls(self):
        for url in ["http://example.org", "https://user:password@example.org", "https://example.org/path",
                    "https://example.org?token=secret", "file:///tmp/test"]:
            with self.assertRaises(ValueError):
                HostClient(url)
        HostClient("https://example.org")
        HostClient("http://127.0.0.1:8000")

    def test_real_http_client_login_csrf_inspect_propose_confirm_and_logout(self):
        with self.Session() as session:
            session.add(AppSetting(key=PIN_SETTING_KEYS["owner"], value=hash_pin("test-maintenance-pin")))
            session.commit()

        def session_override():
            with self.Session() as session:
                yield session

        server = uvicorn.Server(uvicorn.Config(main.app, lifespan="off", log_level="error"))
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen(128)
            client = HostClient(f"http://127.0.0.1:{sock.getsockname()[1]}")
            main.app.dependency_overrides[main.get_session] = session_override
            thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
            try:
                with patch.object(main, "SessionLocal", self.Session), \
                     patch.object(main, "configured_environment_pin_hash", return_value=""), \
                     patch.object(main, "production_mode", return_value=False):
                    thread.start()
                    deadline = time.monotonic() + 10
                    while not server.started and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertTrue(server.started)
                    client.login("test-maintenance-pin")
                    snapshot = client.request("GET", f"/api/admin/maintenance/apartments/{self.apartment_id}")
                    self.assertEqual(snapshot["apartment"]["name"], "К1")
                    proposal = client.request("POST", "/api/admin/maintenance/proposals", {
                        "operation": "update_apartment", "arguments": {"apartment_id": self.apartment_id, "name": "К3"},
                        "reason": "Проверка HTTP-клиента на синтетической базе"})
                    result = client.request("POST", f"/api/hermes/proposals/{proposal['id']}/confirm", {})
                    self.assertEqual(result["status"], "executed")
                    client.logout()
                    with self.assertRaisesRegex(ValueError, "HTTP 401"):
                        client.request("GET", f"/api/admin/maintenance/apartments/{self.apartment_id}")
                    server.should_exit = True
                    thread.join(timeout=10)
            finally:
                server.should_exit = True
                if thread.is_alive():
                    thread.join(timeout=10)
                main.app.dependency_overrides.pop(main.get_session, None)
