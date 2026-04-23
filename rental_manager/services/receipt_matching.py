from __future__ import annotations

import re
from typing import Any


def normalize_telegram_handle(value: str) -> str:
    return (value or "").strip().lstrip("@").lower()


def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    return digits


def normalize_name(value: str) -> str:
    normalized = (value or "").lower().replace("ё", "е")
    normalized = re.sub(r"[^a-zа-я0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def names_loosely_match(left: str, right: str) -> bool:
    left_norm = normalize_name(left)
    right_norm = normalize_name(right)
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm:
        return True
    left_tokens = left_norm.split()
    right_tokens = right_norm.split()
    if left_tokens and right_tokens and left_tokens[0] == right_tokens[0]:
        return True
    return left_norm in right_norm or right_norm in left_norm


def detect_receipt_channel(parsed: dict[str, Any]) -> str:
    transfer_type = (parsed.get("transfer_type") or "").lower()
    recipient_name = (parsed.get("recipient_name") or "").lower()
    recipient_account = parsed.get("recipient_account") or ""
    recipient_phone = parsed.get("recipient_phone") or ""
    if "по номеру телефона" in transfer_type or recipient_phone:
        return "personal"
    if "юридическому лицу" in transfer_type or recipient_account or recipient_name.startswith("ип "):
        return "ip"
    return "unknown"


def receipt_validation_issues(parsed: dict[str, Any], settings: dict[str, Any], channel: str) -> list[str]:
    issues: list[str] = []
    if channel == "ip":
        expected_name = settings.get("ip_recipient_name") or ""
        expected_account = settings.get("ip_recipient_account") or ""
        if expected_name and not names_loosely_match(parsed.get("recipient_name") or "", expected_name):
            issues.append("получатель ИП не совпал с настройками")
        if expected_account and (parsed.get("recipient_account") or "") != expected_account:
            issues.append("счёт получателя ИП не совпал с настройками")
    elif channel == "personal":
        expected_name = settings.get("personal_recipient_name") or ""
        expected_phone = normalize_phone(settings.get("personal_recipient_phone") or "")
        if expected_name and not names_loosely_match(parsed.get("recipient_name") or "", expected_name):
            issues.append("получатель перевода не совпал с настройками")
        if expected_phone and normalize_phone(parsed.get("recipient_phone") or "") != expected_phone:
            issues.append("номер получателя перевода не совпал с настройками")
    return issues


def choose_exact_receipt_match(
    amount: float,
    channel: str,
    rent_candidates: list[dict[str, Any]],
    utility_candidates: list[dict[str, Any]],
) -> tuple[str, int | None, list[str]]:
    if amount <= 0:
        return "review", None, ["в чеке нет положительной суммы"]

    matches: list[tuple[str, int]] = []
    if channel == "ip":
        matches = [("rent", item["id"]) for item in rent_candidates if abs(float(item["debt"]) - amount) <= 0.01]
    elif channel == "personal":
        matches.extend(("rent", item["id"]) for item in rent_candidates if abs(float(item["debt"]) - amount) <= 0.01)
        matches.extend(("utility", item["id"]) for item in utility_candidates if abs(float(item["debt"]) - amount) <= 0.01)
    else:
        return "review", None, ["тип перевода пока не удалось определить"]

    if len(matches) == 1:
        return matches[0][0], matches[0][1], []
    if not matches:
        return "review", None, ["не найдено точного долга под эту сумму"]
    return "review", None, ["нашлось несколько совпадений по сумме, нужна ручная проверка"]
