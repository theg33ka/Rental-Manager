from __future__ import annotations

import json
from pathlib import Path
import urllib.error
import urllib.request
from typing import Any


def parse_command(text: str) -> tuple[str, list[str]]:
    raw = (text or "").strip()
    if not raw:
        return "", []
    parts = raw.split()
    command = parts[0].split("@", 1)[0].lower()
    return command, parts[1:]


def build_status_message(dashboard: dict[str, Any]) -> str:
    lines = [
        "Статус пульта:",
        f"• Открытых месячных отчётов: {len(dashboard.get('monthly_reports', []))}",
        f"• Просроченная аренда: {len(dashboard.get('rent_overdue', []))}",
        f"• Частичная аренда: {len(dashboard.get('rent_partial', []))}",
        f"• Просроченная коммуналка: {len(dashboard.get('utility_overdue', []))}",
        f"• Показания давно не передавались: {len(dashboard.get('stale_readings', []))}",
        f"• Личные расходы ждут компенсации: {len(dashboard.get('pending_personal_expenses', []))}",
        f"• Подозрительные чеки: {len(dashboard.get('suspicious_receipts', []))}",
    ]
    return "\n".join(lines)


def build_reports_message(reports: list[dict[str, Any]]) -> str:
    if not reports:
        return "Все месячные отчёты закрыты. Редкий мирный момент."
    lines = ["Открытые месячные отчёты:"]
    for report in reports:
        lines.append(
            f"• {report['month_name']} {report['year']}: {report['label']} ({report['issue_count']} проблем)"
        )
    return "\n".join(lines)


def app_keyboard(base_url: str, reports: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    rows: list[list[dict[str, Any]]] = []
    if base_url:
        rows.append([{"text": "Открыть пульт", "url": base_url}])
    if reports:
        for report in reports[:3]:
            rows.append(
                [
                    {
                        "text": f"{report['month_name']} {report['year']}",
                        "url": f"{base_url.rstrip('/')}/api/reports/monthly.xlsx?year={report['year']}&month={report['month']}",
                    }
                ]
            )
    if not rows:
        return None
    return {"inline_keyboard": rows}


def owner_commands() -> list[dict[str, str]]:
    return [
        {"command": "start", "description": "Короткая справка по owner-командам"},
        {"command": "id", "description": "Показать текущий chat id"},
        {"command": "status", "description": "Сводка по пульту и долгам"},
        {"command": "reports", "description": "Открытые месячные отчёты"},
        {"command": "run_reminders", "description": "Прогнать напоминания жильцам"},
        {"command": "app", "description": "Кнопка открытия веб-пульта"},
    ]


def telegram_api_request(token: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram API {method} failed: {body}") from exc


def send_message(token: str, chat_id: int | str, text: str, reply_markup: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return telegram_api_request(token, "sendMessage", payload)


def telegram_file_info(token: str, file_id: str) -> dict[str, Any]:
    response = telegram_api_request(token, "getFile", {"file_id": file_id})
    return response.get("result") or {}


def download_telegram_file(token: str, file_path: str, destination: str | Path) -> Path:
    url = f"https://api.telegram.org/file/bot{token}/{file_path.lstrip('/')}"
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=30) as response:
        target.write_bytes(response.read())
    return target
