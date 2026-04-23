from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any


def extract_pdf_text(file_path: str | Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(file_path))
    return "\n".join((page.extract_text() or "") for page in reader.pages).strip()


def parse_receipt_file(file_path: str | Path) -> dict[str, Any]:
    path = Path(file_path)
    text = extract_pdf_text(path)
    parsed = parse_receipt_text(text)
    parsed["file_name"] = path.name
    parsed["file_type"] = path.suffix.lower()
    return parsed


def parse_receipt_text(text: str) -> dict[str, Any]:
    flat = _normalize_spaces(text)
    amount = _money_search(
        flat,
        [
            r"Итого\s+([\d\s]+(?:[.,]\d{2})?)\s*[₽i]",
            r"Сумма\s+([\d\s]+(?:[.,]\d{2})?)\s*[₽i]",
            r"([\d\s]+(?:[.,]\d{2})?)\s*[₽i]\s*Сумма",
        ],
    )
    paid_at = _parse_receipt_datetime(flat)
    transfer_type = _group(flat, r"Перевод\s+(По номеру телефона|Юридическому лицу)")
    status = _group(
        flat,
        r"Статус\s+(.+?)\s+(?:Сч[её]т списания|Сумма|Комиссия|Банк получателя|Банк-\s*получатель)",
    )
    payer_name = (
        _group(flat, r"Плательщик\s+(.+?)\s+Банк-\s*получатель")
        or _group(flat, r"Комиссия\s+Без комиссии\s+(.+?)Отправитель")
    )
    recipient_phone = _group(flat, r"Телефон получателя\s+(.+?)\s+Получатель")
    recipient_bank = (
        _group(flat, r"Банк-\s*получатель\s+(.+?)\s+БИК\s+\d{9}")
        or _group(flat, r"Банк получателя\s+(.+?)\s+(?:Сч[её]т получателя|Сч[её]т списания|Идентификатор операции|Служба поддержки)")
    )
    recipient_bik = _group(flat, r"Банк-\s*получатель\s+.+?\s+БИК\s+(\d{9})")
    correspondent_account = _group(flat, r"Корреспондентский счет\s+(\d+)")
    recipient_account = _group(flat, r"Сч[её]т получателя\s+(\d+)")
    recipient_name = (
        _group(flat, r"Сч[её]т получателя\s+\d+\s+Получатель\s+(.+?)\s+Назначение платежа")
        or _group(flat, r"Получатель\s+(.+?)\s+Назначение платежа")
        or _group(flat, r"Получатель\s+(.+?)\s+Назначение перевода")
        or _group(flat, r"Получатель\s+(.+?)\s+Банк получателя")
    )
    purpose = (
        _group(flat, r"Назначение платежа\s+(.+?)\s+По вопросам зачисления")
        or _group(flat, r"Назначение перевода\s+(.+?)\s+Служба поддержки")
    )
    receipt_number = _group(flat, r"Квитанция\s+№\s*([0-9-]+)")
    source_bank = _detect_source_bank(flat)

    return {
        "amount": amount,
        "paid_at": paid_at.isoformat(timespec="minutes") if paid_at else None,
        "transfer_type": transfer_type or "",
        "status": status or "",
        "is_success": bool(status and "успеш" in status.lower()),
        "payer_name": payer_name or "",
        "recipient_name": recipient_name or "",
        "recipient_phone": recipient_phone or "",
        "recipient_bank": recipient_bank or "",
        "recipient_bik": recipient_bik or "",
        "recipient_account": recipient_account or "",
        "correspondent_account": correspondent_account or "",
        "purpose": purpose or "",
        "receipt_number": receipt_number or "",
        "source_bank": source_bank,
        "raw_text": text,
    }


def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _group(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    return re.sub(r"\s+", " ", match.group(1)).strip(" \n\r\t:;")


def _money_search(text: str, patterns: list[str]) -> float:
    for pattern in patterns:
        value = _group(text, pattern)
        if value:
            return _parse_money(value)
    return 0.0


def _parse_money(value: str) -> float:
    normalized = value.replace("\u00a0", " ").replace(" ", "").replace(",", ".")
    return float(normalized)


def _datetime_search(text: str, pattern: str) -> datetime | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    return datetime.strptime(f"{match.group(1)} {match.group(2)}", "%d.%m.%Y %H:%M")


def _parse_receipt_datetime(text: str) -> datetime | None:
    patterns = [
        r"Перевод\s+(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})",
        r"^(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})(?::\d{2})?",
    ]
    for pattern in patterns:
        result = _datetime_search(text, pattern)
        if result:
            return result
    return None


def _detect_source_bank(text: str) -> str:
    lowered = text.lower()
    if "ozon банка" in lowered or "озон банк" in lowered:
        return "ozon_bank"
    if "fb@tbank.ru" in lowered or "т-банк" in lowered or "тинькофф" in lowered:
        return "tbank"
    if "сбербанк" in lowered:
        return "sberbank"
    return "unknown"
