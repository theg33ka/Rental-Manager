from __future__ import annotations

import unittest
from datetime import date

from rental_manager.main import iter_generated_report_months, month_range, parse_tiers, report_status_label, resolve_deferral_until


class MainHelperTests(unittest.TestCase):
    def test_parse_tiers_accepts_colons_commas_and_infinity_marker(self) -> None:
        tiers = parse_tiers("1000:4.2; 4000:4,5; *:7")

        self.assertEqual(
            tiers,
            [
                {"limit": 1000.0, "price": 4.2},
                {"limit": 4000.0, "price": 4.5},
                {"limit": None, "price": 7.0},
            ],
        )

    def test_parse_tiers_adds_open_ended_last_tier_when_missing(self) -> None:
        tiers = parse_tiers("1000:4.2; 4000:4.5")

        self.assertEqual(tiers[-1], {"limit": None, "price": 4.5})

    def test_parse_tiers_accepts_open_ended_price_without_limit(self) -> None:
        tiers = parse_tiers('"1000":"4.18", "2200":"4.7", "3000":"7", "9"')

        self.assertEqual(
            tiers,
            [
                {"limit": 1000.0, "price": 4.18},
                {"limit": 2200.0, "price": 4.7},
                {"limit": 3000.0, "price": 7.0},
                {"limit": None, "price": 9.0},
            ],
        )

    def test_parse_tiers_rejects_empty_value(self) -> None:
        with self.assertRaisesRegex(ValueError, "пуст"):
            parse_tiers("")

    def test_resolve_deferral_until_accepts_days(self) -> None:
        result = resolve_deferral_until({"deferral_days": "5"}, today=date(2026, 4, 23))

        self.assertEqual(result, date(2026, 4, 28))

    def test_resolve_deferral_until_rejects_non_positive_days(self) -> None:
        with self.assertRaisesRegex(ValueError, "больше нуля"):
            resolve_deferral_until({"deferral_days": "0"}, today=date(2026, 4, 23))

    def test_resolve_deferral_until_keeps_date_fallback_for_existing_clients(self) -> None:
        result = resolve_deferral_until({"deferral_until": "2026-05-02"}, today=date(2026, 4, 23))

        self.assertEqual(result, date(2026, 5, 2))

    def test_report_status_label_translates_known_statuses(self) -> None:
        self.assertEqual(report_status_label("paid"), "оплачено")
        self.assertEqual(report_status_label("partial"), "частично оплачено")

    def test_report_status_label_adds_overdue_days(self) -> None:
        result = report_status_label("overdue", due_date=date(2026, 4, 20), today=date(2026, 4, 23))

        self.assertEqual(result, "просрочено, 3 дн.")

    def test_month_range_returns_calendar_month(self) -> None:
        self.assertEqual(month_range(2026, 2), (date(2026, 2, 1), date(2026, 2, 28)))

    def test_generated_report_months_start_with_january_2026(self) -> None:
        self.assertEqual(iter_generated_report_months(date(2026, 4, 23)), [(2026, 1), (2026, 2), (2026, 3)])


if __name__ == "__main__":
    unittest.main()
