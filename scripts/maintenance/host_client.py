"""Доступ к хосту через owner-сессию, CSRF и подтверждаемые backend operations."""
from __future__ import annotations

import argparse
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Перенаправление запрещено: проверьте URL хоста")


class HostClient:
    def __init__(self, base_url: str):
        parsed = urlsplit(base_url)
        local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if (parsed.scheme != "https" and not (parsed.scheme == "http" and local)) or not parsed.hostname:
            raise ValueError("Для хоста требуется HTTPS; HTTP разрешён только для localhost")
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise ValueError("Укажите только origin хоста, без пути, параметров и пароля")
        self.base_url = base_url.rstrip("/")
        self.csrf = ""
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()), NoRedirect())

    def request(self, method: str, path: str, payload=None):
        headers = {"Accept": "application/json", "User-Agent": "RentalManagerMaintenance/1"}
        if self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as exc:
            # Не выводим тело ошибки: оно может содержать входные финансовые данные.
            raise ValueError(f"Хост вернул HTTP {exc.code}; проверьте доступ и параметры операции") from None

    def login(self, pin: str):
        result = self.request("POST", "/api/auth/pin", {"pin_code": pin, "remember_device": False})
        self.csrf = result.get("csrf_token", "")
        if result.get("role") != "owner":
            self.logout()
            raise ValueError("Нужна сессия владельца")

    def logout(self):
        if self.csrf:
            try:
                self.request("POST", "/api/auth/logout", {})
            finally:
                self.csrf = ""


def main():
    parser = argparse.ArgumentParser(description="Диагностика и подтверждаемые исправления на хосте")
    parser.add_argument("--url", default=os.environ.get("RENTAL_MAINTENANCE_URL", ""))
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("apartment_id", type=int)
    inspect.add_argument("--limit", type=int, default=100)
    propose = sub.add_parser("propose")
    propose.add_argument("file", type=Path)
    sub.add_parser("proposals")
    confirm = sub.add_parser("confirm")
    confirm.add_argument("proposal_id", type=int)
    confirm.add_argument("--apply", action="store_true", required=True)
    args = parser.parse_args()
    pin = os.environ.get("RENTAL_MAINTENANCE_PIN", "")
    if not pin:
        raise ValueError("Задайте RENTAL_MAINTENANCE_PIN в окружении процесса; не передавайте PIN аргументом")
    client = HostClient(args.url)
    try:
        client.login(pin)
        if args.command == "inspect":
            result = client.request("GET", f"/api/admin/maintenance/apartments/{args.apartment_id}?limit={args.limit}")
        elif args.command == "propose":
            payload = json.loads(args.file.read_text(encoding="utf-8-sig"))
            result = client.request("POST", "/api/admin/maintenance/proposals", payload)
        elif args.command == "confirm":
            proposals = client.request("GET", "/api/hermes/proposals")
            proposal = next((item for item in proposals if item["id"] == args.proposal_id), None)
            if not proposal or proposal.get("requested_by") != "maintenance":
                raise ValueError("Через этот клиент можно подтверждать только предложения обслуживания из текущего списка")
            result = client.request("POST", f"/api/hermes/proposals/{args.proposal_id}/confirm", {"confirmation_pin": pin})
        else:
            result = client.request("GET", "/api/hermes/proposals")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        client.logout()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
