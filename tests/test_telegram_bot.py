from __future__ import annotations

import unittest

from rental_manager.services.telegram_bot import (
    app_keyboard,
    build_reports_message,
    build_status_message,
    owner_commands,
    parse_command,
)


class TelegramBotHelpersTests(unittest.TestCase):
    def test_parse_command_ignores_bot_suffix(self) -> None:
        self.assertEqual(parse_command("/status@rent_helper_bot now"), ("/status", ["now"]))

    def test_build_status_message_uses_dashboard_counts(self) -> None:
        dashboard = {
            "monthly_reports": [1, 2],
            "rent_overdue": [1],
            "rent_partial": [1, 2, 3],
            "utility_overdue": [],
            "stale_readings": [1],
            "pending_personal_expenses": [1, 2],
            "suspicious_receipts": [],
        }
        text = build_status_message(dashboard)

        self.assertIn("Открытых месячных отчётов: 2", text)
        self.assertIn("Частичная аренда: 3", text)

    def test_build_reports_message_lists_open_reports(self) -> None:
        text = build_reports_message(
            [
                {"month_name": "январь", "year": 2026, "label": "много проблем", "issue_count": 5},
                {"month_name": "февраль", "year": 2026, "label": "есть вопросы", "issue_count": 2},
            ]
        )

        self.assertIn("январь 2026", text)
        self.assertIn("февраль 2026", text)

    def test_app_keyboard_contains_open_app_button(self) -> None:
        keyboard = app_keyboard("https://example.com", [])

        self.assertEqual(keyboard["inline_keyboard"][0][0]["url"], "https://example.com")

    def test_owner_commands_include_status_and_reports(self) -> None:
        commands = owner_commands()

        self.assertIn({"command": "status", "description": "Сводка по пульту и долгам"}, commands)
        self.assertIn({"command": "reports", "description": "Открытые месячные отчёты"}, commands)


if __name__ == "__main__":
    unittest.main()
