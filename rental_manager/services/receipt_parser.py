from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any


MONTHS_RU = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}


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
    normalized_text = _cleanup_text(text)
    flat = _normalize_spaces(normalized_text)
    source_bank = _detect_source_bank(normalized_text)
    transfer_type = _parse_transfer_type(flat, source_bank)
    status = _parse_status(flat, source_bank)

    return {
        "amount": _parse_amount(flat, source_bank),
        "paid_at": _format_paid_at(_parse_receipt_datetime(normalized_text, flat)),
        "transfer_type": transfer_type,
        "status": status,
        "is_success": _is_success(status, source_bank, normalized_text),
        "payer_name": _parse_payer_name(flat),
        "recipient_name": _parse_recipient_name(flat, source_bank),
        "recipient_phone": _parse_recipient_phone(flat),
        "recipient_bank": _parse_recipient_bank(flat),
        "recipient_bik": _group(flat, r"\bБИК\s+(\d{9})\b") or "",
        "recipient_account": _parse_recipient_account(flat),
        "correspondent_account": _group(
            flat,
            r"(?:Корр(?:еспондентский)?\.?\s*сч[её]т|Корреспондентский сч[её]т)\s+(\d+)",
        )
        or "",
        "purpose": _parse_purpose(flat),
        "receipt_number": _parse_receipt_number(flat),
        "source_bank": source_bank,
        "raw_text": normalized_text,
    }


def _cleanup_text(text: str) -> str:
    cleaned = (text or "").replace("\u00a0", " ").replace("\u200b", "").replace("\ufeff", "")
    return cleaned.strip()


def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _group(text: str, pattern: str, *, multiline: bool = False) -> str | None:
    flags = re.IGNORECASE | re.DOTALL
    if multiline:
        flags |= re.MULTILINE
    match = re.search(pattern, text, flags=flags)
    if not match:
        return None
    return re.sub(r"\s+", " ", match.group(1)).strip(" \n\r\t:;,.")


def _parse_amount(flat: str, source_bank: str) -> float:
    patterns = [
        r"Сумма платежа\s+([\d\s]+(?:[.,]\d{2})?)\s*[₽i]",
        r"Сумма перевода\s+([\d\s]+(?:[.,]\d{2})?)\s*[₽i]",
    ]
    if source_bank != "sberbank":
        patterns.append(r"Итого\s+([\d\s]+(?:[.,]\d{2})?)\s*[₽i]")
    patterns.extend(
        [
            r"([\d\s]+(?:[.,]\d{2})?)\s*[₽i]\s*Сумма",
            r"Итого\s+([\d\s]+(?:[.,]\d{2})?)\s*[₽i]",
        ]
    )
    for pattern in patterns:
        value = _group(flat, pattern)
        if value:
            return _parse_money(value)
    return 0.0


def _parse_money(value: str) -> float:
    normalized = value.replace(" ", "").replace(",", ".")
    return float(normalized)


def _parse_receipt_datetime(text: str, flat: str) -> datetime | None:
    direct_patterns = [
        r"Перевод\s+(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})",
        r"^(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})(?::\d{2})?",
    ]
    for pattern in direct_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
        if match:
            return datetime.strptime(f"{match.group(1)} {match.group(2)}", "%d.%m.%Y %H:%M")

    month_match = re.search(
        r"(\d{1,2})\s+([А-Яа-я]+)\s+(\d{4})\s+(\d{2}:\d{2})(?::\d{2})?",
        flat,
        flags=re.IGNORECASE,
    )
    if not month_match:
        return None
    day = int(month_match.group(1))
    month = MONTHS_RU.get(month_match.group(2).lower())
    year = int(month_match.group(3))
    if not month:
        return None
    return datetime.strptime(f"{day:02d}.{month:02d}.{year} {month_match.group(4)}", "%d.%m.%Y %H:%M")


def _format_paid_at(value: datetime | None) -> str | None:
    return value.isoformat(timespec="minutes") if value else None


def _parse_transfer_type(flat: str, source_bank: str) -> str:
    value = _group(flat, r"Перевод\s+(По номеру телефона|Юридическому лицу)")
    if value:
        return value
    if "ОПЛАТА ПО РЕКВИЗИТАМ" in flat.upper():
        return "По реквизитам"
    if source_bank == "sberbank" and "Перевод клиенту СберБанка" in flat:
        return "По номеру телефона"
    return ""


def _parse_status(flat: str, source_bank: str) -> str:
    explicit = _group(flat, r"Статус\s+(.+?)\s+(?:Сч[её]т списания|Сумма|Комиссия|Банк получателя|Квитанция)")
    if explicit:
        return explicit
    if source_bank == "sberbank" and "Чек по операции" in flat:
        return "Успешно"
    return ""


def _is_success(status: str, source_bank: str, text: str) -> bool:
    lowered = (status or "").lower()
    if lowered:
        return "успеш" in lowered or "выполн" in lowered
    return source_bank == "sberbank" and "Чек по операции" in text


def _parse_payer_name(flat: str) -> str:
    patterns = [
        r"Плательщик\s+(.+?)\s+(?:Банк-|Способ оплаты|ОПЛАТА ПО РЕКВИЗИТАМ)",
        r"ФИО отправителя\s+(.+?)\s+Сч[её]т отправителя",
        r"Комиссия\s+Без комиссии\s+(.+?)Отправитель",
        r"(.+?)Отправитель\s+Телефон получателя",
    ]
    for pattern in patterns:
        value = _group(flat, pattern)
        if value:
            return value
    return ""


def _parse_recipient_name(flat: str, source_bank: str) -> str:
    patterns = [
        r"Сч[её]т получателя\s+\d+\s+Получатель\s+(.+?)\s+Назначение платежа",
        r"Получатель\s+(.+?)\s+Назначение платежа",
        r"Получатель\s+(.+?)\s+Назначение перевода",
        r"Получатель\s+(.+?)\s+Банк получателя",
        r"ФИО получателя\s+(.+?)\s+Телефон получателя",
        r"Получатель\s+(.+?)\s+ИНН",
    ]
    for pattern in patterns:
        value = _group(flat, pattern)
        if value:
            return value
    if source_bank == "sberbank":
        return _group(flat, r"ФИО получателя\s+(.+?)\s+Телефон получателя") or ""
    return ""


def _parse_recipient_phone(flat: str) -> str:
    return (
        _group(
            flat,
            r"Телефон получателя\s+(.+?)\s+(?:Получатель|ФИО отправителя|Номер сч[её]та получателя)",
        )
        or _group(flat, r"Телефон получателя\s+([+()\d\s-]+)")
        or ""
    )


def _parse_recipient_bank(flat: str) -> str:
    patterns = [
        r"Банк-\s*получатель\s+(.+?)\s+БИК\s+\d{9}",
        r"Банк получателя\s+(.+?)\s+(?:Сч[её]т получателя|Счет получателя|Идентификатор операции|Служба поддержки|Сч[её]т списания)",
    ]
    for pattern in patterns:
        value = _group(flat, pattern)
        if value:
            return value
    return ""


def _parse_recipient_account(flat: str) -> str:
    direct = _group(flat, r"(?:Сч[её]т получателя|Счет получателя)\s+(\d+)")
    if direct:
        return direct
    return _group(flat, r"БИК\s+\d{9}\s+(\d{20})\s+Расч[её]тный сч[её]т") or ""


def _parse_purpose(flat: str) -> str:
    patterns = [
        r"Назначение платежа\s+(.+?)\s+(?:По вопросам зачисления|Идентификатор платежа)",
        r"Назначение перевода\s+(.+?)\s+Служба поддержки",
    ]
    for pattern in patterns:
        value = _group(flat, pattern)
        if value:
            return value
    return ""


def _parse_receipt_number(flat: str) -> str:
    return (
        _group(flat, r"Квитанция\s+№\s*([0-9-]+)")
        or _group(flat, r"Номер документа\s+([0-9-]+)")
        or ""
    )


def _detect_source_bank(text: str) -> str:
    lowered = text.lower()
    if "ozon банка" in lowered or "озон банк" in lowered:
        return "ozon_bank"
    if "fb@tbank.ru" in lowered or "т-банк" in lowered or "тинькофф" in lowered:
        return "tbank"
    if "сбербанк" in lowered or "сбербанка" in lowered:
        return "sberbank"
    return "unknown"
