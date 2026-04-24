from __future__ import annotations

import unittest

from rental_manager.services.receipt_matching import (
    choose_exact_receipt_match,
    detect_receipt_channel,
    names_loosely_match,
    receipt_validation_issues,
)


class ReceiptMatchingTests(unittest.TestCase):
    def test_detects_ip_receipt_by_account(self) -> None:
        parsed = {"recipient_account": "40802810644050156191"}

        self.assertEqual(detect_receipt_channel(parsed), "ip")

    def test_detects_personal_receipt_by_phone_transfer(self) -> None:
        parsed = {"transfer_type": "По номеру телефона"}

        self.assertEqual(detect_receipt_channel(parsed), "personal")

    def test_ip_validation_checks_name_and_account_only(self) -> None:
        issues = receipt_validation_issues(
            {
                "recipient_name": "ИП Кто-то Другой",
                "recipient_account": "123",
                "recipient_bik": "999",
            },
            {
                "ip_recipient_name": "ИП Чантурия Эраст Митридатович",
                "ip_recipient_account": "40802810644050156191",
                "ip_recipient_bik": "045004641",
            },
            "ip",
        )

        self.assertIn("счёт получателя ИП не совпал с настройками", issues)
        self.assertEqual(len(issues), 1)

    def test_personal_validation_checks_name_phone_and_bank(self) -> None:
        issues = receipt_validation_issues(
            {
                "recipient_name": "Кто-то другой",
                "recipient_phone": "+7 999 000-00-00",
                "recipient_bank": "Т-Банк",
            },
            {
                "personal_recipient_name": "Эрнест К.",
                "personal_recipient_phone": "+7 913 385-44-41",
                "personal_recipient_bank": "Сбербанк",
            },
            "personal",
        )

        self.assertEqual(len(issues), 3)

    def test_ip_validation_accepts_sber_legal_form_prefix(self) -> None:
        issues = receipt_validation_issues(
            {
                "recipient_name": "ИНДИВИДУАЛЬНЫЙ ПРЕДПРИНИМАТЕЛЬ ЧАНТУРИЯ ЭРАСТ МИТРИДАТОВИЧ",
                "recipient_account": "40802810644050156191",
                "recipient_bik": "045004641",
            },
            {
                "ip_recipient_name": "ИП Чантурия Эраст Митридатович",
                "ip_recipient_account": "40802810644050156191",
                "ip_recipient_bik": "045004641",
            },
            "ip",
        )

        self.assertEqual(issues, [])

    def test_personal_validation_does_not_fail_when_bank_is_missing_in_receipt(self) -> None:
        issues = receipt_validation_issues(
            {
                "recipient_name": "Марьяна Сергеевна С",
                "recipient_phone": "+7(913)206-02-94",
                "recipient_bank": "",
            },
            {
                "personal_recipient_name": "Марьяна С.",
                "personal_recipient_phone": "+7 913 206-02-94",
                "personal_recipient_bank": "Сбербанк",
            },
            "personal",
        )

        self.assertEqual(issues, [])

    def test_name_matching_understands_ip_long_form(self) -> None:
        self.assertTrue(
            names_loosely_match(
                "ИНДИВИДУАЛЬНЫЙ ПРЕДПРИНИМАТЕЛЬ ЧАНТУРИЯ ЭРАСТ МИТРИДАТОВИЧ",
                "ИП Чантурия Эраст Митридатович",
            )
        )

    def test_exact_match_prefers_single_rent_match(self) -> None:
        match_type, match_id, issues = choose_exact_receipt_match(
            20000.0,
            "ip",
            [{"id": 7, "debt": 20000.0}],
            [],
        )

        self.assertEqual((match_type, match_id, issues), ("rent", 7, []))

    def test_exact_match_requests_review_when_amount_hits_multiple_candidates(self) -> None:
        match_type, match_id, issues = choose_exact_receipt_match(
            5000.0,
            "personal",
            [{"id": 1, "debt": 5000.0}],
            [{"id": 2, "debt": 5000.0}],
        )

        self.assertEqual(match_type, "review")
        self.assertIsNone(match_id)
        self.assertTrue(issues)


if __name__ == "__main__":
    unittest.main()
