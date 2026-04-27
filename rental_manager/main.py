from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from io import BytesIO
from pathlib import Path
import threading
import time as time_module
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from rental_manager.database import ROOT_DIR, SessionLocal, get_session, init_db
from rental_manager.models import (
    Apartment,
    Expense,
    Lease,
    MessageLog,
    Meter,
    MeterReading,
    PaymentReceipt,
    RentalObject,
    RentCharge,
    Tariff,
    Tenant,
    UtilityBill,
    UtilityBillLine,
    UtilityService,
    utc_now,
    AppSetting,
)
from rental_manager.services.billing import (
    IGNORE_LEASE_MARK,
    RENT_GENERATION_START,
    active_lease_for_apartment,
    calculate_utility_bill,
    effective_due_date,
    full_months_lived,
    generate_rent_charges,
    money,
    next_due_after,
    object_last_reading_date,
    parse_date,
    resident_due_date,
    status_for_amount,
    update_rent_charge_status,
    update_utility_line_status,
)
from rental_manager.services.payment_allocation import (
    EPS,
    build_rent_plan,
    build_utility_plan,
    create_rent_receipts,
    create_utility_receipts,
    describe_rent_allocation_decision,
    recalculate_lease_balances,
    rent_charge_candidates,
    utility_line_candidates,
)
from rental_manager.services.receipt_matching import (
    detect_receipt_channel,
    normalize_telegram_handle,
    receipt_validation_issues,
)
from rental_manager.services.receipt_parser import parse_receipt_file
from rental_manager.services.seed import seed_if_empty, seed_release_baseline_if_empty
from rental_manager.services.telegram_bot import (
    app_keyboard,
    build_reports_message,
    build_status_message,
    copy_message,
    download_telegram_file,
    owner_commands,
    parse_command,
    send_message,
    tenant_keyboard,
    telegram_file_info,
    telegram_api_request,
)


app = FastAPI(title="Rental Manager", version="0.1.0")
app.mount("/static", StaticFiles(directory=ROOT_DIR / "static"), name="static")


DEFAULT_SETTINGS = {
    "color_palette": "classic",
    "app_base_url": "",
    "telegram_owner_chat_id": "",
    "notifications_enabled": False,
    "notification_cutoff_date": "",
    "automation_rent_due_cadence": "twice_daily",
    "automation_rent_overdue_cadence": "twice_daily",
    "automation_utility_cadence": "daily_evening",
    "ip_recipient_name": "ИНДИВИДУАЛЬНЫЙ ПРЕДПРИНИМАТЕЛЬ ЧАНТУРИЯ ЭРАСТ МИТРИДАТОВИЧ",
    "ip_recipient_inn": "540506055229",
    "ip_recipient_ogrnip": "324508100223397",
    "ip_recipient_account": "40802810644050156191",
    "ip_recipient_bank": "СИБИРСКИЙ БАНК ПАО СБЕРБАНК",
    "ip_recipient_bik": "045004641",
    "ip_recipient_correspondent_account": "30101810500000000641",
    "ip_recipient_bank_inn": "7707083893",
    "ip_recipient_bank_kpp": "540643001",
    "personal_recipient_name": "",
    "personal_recipient_phone": "",
    "personal_recipient_bank": "",
    "message_rent_due": "Здравствуйте. Напоминаю: по квартире {apartment} сегодня ожидается оплата аренды. ИП: {ip_due}. Перевод: {personal_due}. Итого: {total_due}.",
    "message_rent_overdue": "Здравствуйте. По квартире {apartment} сейчас просрочена аренда. Долг: {debt}. ИП: {ip_due}. Перевод: {personal_due}.",
    "message_utility_bill": "Здравствуйте. Сформированы счета по коммунальным платежам.\n{utility_debt_details}\n\nВсего: {utility_total}. Срок оплаты до {utility_due_date}. Оплата переводом на {personal_recipient_phone_text} {personal_recipient_bank_text}.\n\nОтправьте чеки в этот чат в виде документа, скриншоты больше не принимаются.\n(История платежей в приложении банка -> чек об операции -> сохранить или отправить)",
    "message_all_debts": "Здравствуйте. Уведомляем вас о наличии следующих задолженностей:\n{all_debts_breakdown}\n\nОтправьте чеки в этот чат в виде документа, скриншоты больше не принимаются.\n(История платежей в приложении банка -> чек об операции -> сохранить или отправить)",
    "message_receipt_received": "Чек получил и сохранил. Если всё совпало, я это отмечу. Если нет, передам владельцу на ручную проверку.",
    "message_receipt_review": "Чек получил, но там есть вопросы. Я уже отправил его владельцу на проверку.",
    "message_receipt_duplicate": "Этот чек уже есть в системе. Повторно засчитывать его не буду.",
    "message_owner_receipt_alert": "Новый чек от {tenant_name}. Квартира: {apartment}. Сумма: {amount}. Канал: {channel}. Статус: {receipt_status}. {receipt_summary}",
}
SECRET_SETTINGS = {
    "telegram_bot_token": "",
    "telegram_webhook_secret": "",
}
ALL_SETTINGS = {**DEFAULT_SETTINGS, **SECRET_SETTINGS}
INTERNAL_SETTINGS = {
    "telegram_tenant_links": "{}",
    "ignored_lease_ids": "[]",
}
BOOLEAN_SETTINGS = {"notifications_enabled"}
VALID_REMINDER_CADENCES = {"twice_daily", "daily_evening", "every_two_days", "never"}
REMINDER_CADENCE_KEYS = {
    "message_rent_due": "automation_rent_due_cadence",
    "message_rent_overdue": "automation_rent_overdue_cadence",
    "message_utility_bill": "automation_utility_cadence",
}
LEASE_AUTOMATION_TEMPLATES = ("message_rent_due", "message_rent_overdue", "message_utility_bill")
EXPENSE_FUND_RECEIPT_MARK = "PAYMENT_RECEIPT:"
REMINDER_WORKER_STARTED = False
REMINDER_WORKER_INTERVAL_SECONDS = 900
LOCAL_TZ = ZoneInfo("Asia/Novosibirsk")
DEFAULT_FALLBACK_KEYS = {
    "ip_recipient_name",
    "ip_recipient_inn",
    "ip_recipient_ogrnip",
    "ip_recipient_account",
    "ip_recipient_bank",
    "ip_recipient_bik",
    "ip_recipient_correspondent_account",
    "ip_recipient_bank_inn",
    "ip_recipient_bank_kpp",
}

REPORT_START_MONTH = date(2026, 1, 1)
MONTH_NAMES = [
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
    "июнь",
    "июль",
    "август",
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
]


@app.on_event("startup")
def startup() -> None:
    init_db()
    with SessionLocal() as session:
        seeded_release = seed_release_baseline_if_empty(session)
        if not seeded_release:
            seed_if_empty(session)
        ensure_runtime_defaults(session)
        generate_rent_charges(session)
        session.commit()
    start_reminder_worker()


def reminder_worker_loop() -> None:
    # Первый сон нужен, чтобы редеплой не отправлял пачку сообщений в ту же секунду.
    time_module.sleep(60)
    while True:
        try:
            with SessionLocal() as session:
                run_due_reminders(session)
                session.commit()
        except Exception as exc:
            print(f"[REMINDERS] background worker failed: {exc}")
        time_module.sleep(REMINDER_WORKER_INTERVAL_SECONDS)


def start_reminder_worker() -> None:
    global REMINDER_WORKER_STARTED
    if REMINDER_WORKER_STARTED:
        return
    REMINDER_WORKER_STARTED = True
    thread = threading.Thread(target=reminder_worker_loop, name="rental-reminders", daemon=True)
    thread.start()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT_DIR / "static" / "index.html")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


def current_month_range() -> tuple[date, date]:
    today = date.today()
    start = today.replace(day=1)
    if today.month == 12:
        end = date(today.year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(today.year, today.month + 1, 1) - timedelta(days=1)
    return start, end


def month_range(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    return start, end


def iter_generated_report_months(today: date | None = None) -> list[tuple[int, int]]:
    today = today or date.today()
    last_complete = today.replace(day=1) - timedelta(days=1)
    if last_complete < REPORT_START_MONTH:
        return []

    months: list[tuple[int, int]] = []
    year = REPORT_START_MONTH.year
    month = REPORT_START_MONTH.month
    while date(year, month, 1) <= last_complete.replace(day=1):
        months.append((year, month))
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1
    return months


def setting_bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def ensure_runtime_defaults(session: Session) -> None:
    changed = False
    if not session.get(AppSetting, "notifications_enabled"):
        session.add(AppSetting(key="notifications_enabled", value="0"))
        changed = True
    if not session.get(AppSetting, "notification_cutoff_date"):
        session.add(AppSetting(key="notification_cutoff_date", value=date.today().isoformat()))
        changed = True
    if changed:
        session.flush()


def get_setting_value(session: Session, key: str) -> str:
    dynamic_key = key.startswith("lease_") and "_cadence_" in key
    if key not in ALL_SETTINGS and key not in INTERNAL_SETTINGS and not dynamic_key:
        return ""
    row = session.get(AppSetting, key)
    if row:
        return row.value
    if key in ALL_SETTINGS:
        return str(ALL_SETTINGS[key])
    if dynamic_key:
        return ""
    return str(INTERNAL_SETTINGS[key])


def get_settings(session: Session) -> dict[str, str | bool]:
    settings: dict[str, str | bool] = {}
    for key in DEFAULT_SETTINGS:
        raw = get_setting_value(session, key)
        if key in DEFAULT_FALLBACK_KEYS and raw == "":
            raw = str(DEFAULT_SETTINGS[key])
        settings[key] = setting_bool_value(raw) if key in BOOLEAN_SETTINGS else raw
    for key in SECRET_SETTINGS:
        settings[f"{key}_configured"] = bool(get_setting_value(session, key))
    return settings


def save_settings(session: Session, payload: dict[str, Any]) -> dict[str, str | bool]:
    allowed = set(ALL_SETTINGS)
    for key, value in payload.items():
        if key not in allowed:
            continue
        if key in SECRET_SETTINGS and value in {"", None}:
            continue
        setting = session.get(AppSetting, key)
        if not setting:
            setting = AppSetting(key=key)
            session.add(setting)
        if key in BOOLEAN_SETTINGS:
            setting.value = "1" if setting_bool_value(value) else "0"
        elif key == "notification_cutoff_date" and not value:
            setting.value = date.today().isoformat()
        else:
            setting.value = str(value)
    session.commit()
    return get_settings(session)


def telegram_token(session: Session) -> str:
    return get_setting_value(session, "telegram_bot_token").strip()


def telegram_secret(session: Session) -> str:
    return get_setting_value(session, "telegram_webhook_secret").strip()


def telegram_owner_chat_id(session: Session) -> str:
    return get_setting_value(session, "telegram_owner_chat_id").strip()


def app_base_url(session: Session) -> str:
    return get_setting_value(session, "app_base_url").strip().rstrip("/")


def owner_chat_allowed(session: Session, chat_id: int | str) -> bool:
    owner_id = telegram_owner_chat_id(session)
    return bool(owner_id) and str(chat_id) == owner_id


def notifications_enabled(session: Session) -> bool:
    return setting_bool_value(get_setting_value(session, "notifications_enabled"))


def notification_cutoff_date(session: Session) -> date:
    raw = get_setting_value(session, "notification_cutoff_date").strip()
    if raw:
        return parse_date(raw, date.today())
    return date.today()


def configured_notification_cutoff_date(session: Session) -> date | None:
    setting = session.get(AppSetting, "notification_cutoff_date")
    raw = (setting.value or "").strip() if setting else ""
    if not raw:
        return None
    try:
        return parse_date(raw, date.today())
    except ValueError:
        return None


def debt_visible_by_cutoff(due_date: date | None, cutoff: date | None) -> bool:
    if not cutoff or not due_date:
        return True
    return due_date >= cutoff


def allocation_cutoff_date(session: Session) -> date:
    cutoff = configured_notification_cutoff_date(session)
    if not cutoff or cutoff < RENT_GENERATION_START:
        return RENT_GENERATION_START
    return cutoff


def ignored_lease_ids(session: Session) -> set[int]:
    raw = get_setting_value(session, "ignored_lease_ids") or "[]"
    try:
        values = json.loads(raw)
    except json.JSONDecodeError:
        return set()
    if not isinstance(values, list):
        return set()
    result: set[int] = set()
    for value in values:
        try:
            result.add(int(value))
        except (TypeError, ValueError):
            continue
    return result


def save_ignored_lease_ids(session: Session, values: set[int]) -> None:
    setting = session.get(AppSetting, "ignored_lease_ids")
    if not setting:
        setting = AppSetting(key="ignored_lease_ids")
        session.add(setting)
    setting.value = json.dumps(sorted(values), ensure_ascii=False)


def lease_ignored(session: Session | None, lease_id: int | None) -> bool:
    if not session or not lease_id:
        return False
    lease = session.get(Lease, int(lease_id))
    return int(lease_id) in ignored_lease_ids(session) or bool(lease and IGNORE_LEASE_MARK in (lease.notes or ""))


def set_lease_ignored(session: Session, lease_id: int, ignored: bool) -> None:
    values = ignored_lease_ids(session)
    if ignored:
        values.add(int(lease_id))
    else:
        values.discard(int(lease_id))
    save_ignored_lease_ids(session, values)
    lease = session.get(Lease, lease_id)
    if lease:
        notes = lease.notes or ""
        if ignored and IGNORE_LEASE_MARK not in notes:
            lease.notes = (notes + "\n" + IGNORE_LEASE_MARK).strip()
        if not ignored and IGNORE_LEASE_MARK in notes:
            lease.notes = "\n".join(line for line in notes.splitlines() if line.strip() != IGNORE_LEASE_MARK).strip()


def operational_lease(session: Session, lease: Lease | None) -> bool:
    return bool(lease and lease.active and lease.apartment.active and not lease_ignored(session, lease.id))


def reminder_cadence(session: Session, template_key: str, lease_id: int | None = None) -> str:
    # Сначала проверяем per-lease настройку
    if lease_id:
        lease_cadence = get_lease_cadence(session, lease_id, template_key)
        if lease_cadence:
            return lease_cadence
    # Иначе используем глобальную настройку
    key = REMINDER_CADENCE_KEYS.get(template_key, "")
    if not key:
        return "twice_daily"
    value = str(get_setting_value(session, key) or "").strip()
    if value in VALID_REMINDER_CADENCES:
        return value
    return str(DEFAULT_SETTINGS[key])


def get_lease_cadence(session: Session, lease_id: int, template_key: str) -> str | None:
    """Получить индивидуальную настройку cadence для арендатора."""
    key = f"lease_{lease_id}_cadence_{template_key}"
    value = get_setting_value(session, key)
    if value and value.strip() in VALID_REMINDER_CADENCES:
        return value.strip()
    return None


def set_lease_cadence(session: Session, lease_id: int, template_key: str, cadence: str) -> None:
    """Сохранить индивидуальную настройку cadence для арендатора."""
    key = f"lease_{lease_id}_cadence_{template_key}"
    setting = session.get(AppSetting, key)
    if not setting:
        setting = AppSetting(key=key)
        session.add(setting)
    setting.value = cadence


def clear_lease_cadence(session: Session, lease_id: int, template_key: str) -> None:
    """Удалить индивидуальную настройку cadence (сбросить на значение по умолчанию)."""
    key = f"lease_{lease_id}_cadence_{template_key}"
    setting = session.get(AppSetting, key)
    if setting:
        session.delete(setting)


def get_lease_automation(session: Session, lease_id: int) -> dict[str, str]:
    return {
        template_key: get_lease_cadence(session, lease_id, template_key) or "inherit"
        for template_key in LEASE_AUTOMATION_TEMPLATES
    }


def save_lease_automation(session: Session, lease_id: int, payload: dict[str, Any]) -> dict[str, str]:
    for template_key in LEASE_AUTOMATION_TEMPLATES:
        value = str(payload.get(template_key) or "inherit").strip()
        if value in {"", "inherit"}:
            clear_lease_cadence(session, lease_id, template_key)
            continue
        if value not in VALID_REMINDER_CADENCES:
            raise HTTPException(400, "Некорректная частота уведомлений")
        set_lease_cadence(session, lease_id, template_key, value)
    return get_lease_automation(session, lease_id)


def cadence_label(value: str) -> str:
    return {
        "twice_daily": "2 раза в день",
        "daily_evening": "вечером каждый день",
        "every_two_days": "раз в два дня",
        "never": "выключено",
    }.get(value, value)


def local_now() -> datetime:
    return datetime.now(LOCAL_TZ)


def to_local_time(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(LOCAL_TZ)


def cadence_slots_for_day(day: date, cadence: str) -> list[datetime]:
    if cadence == "twice_daily":
        return [
            datetime.combine(day, time(hour=12), tzinfo=LOCAL_TZ),
            datetime.combine(day, time(hour=20), tzinfo=LOCAL_TZ),
        ]
    return [datetime.combine(day, time(hour=20), tzinfo=LOCAL_TZ)]


def next_reminder_slot(cadence: str, latest: datetime | None, now: datetime | None = None) -> datetime:
    now = now or local_now()
    if cadence == "never":
        return datetime.max.replace(tzinfo=LOCAL_TZ)
    latest_local = to_local_time(latest)
    check_day = now.date()
    for _ in range(8):
        for slot in cadence_slots_for_day(check_day, cadence):
            if slot <= now:
                if latest_local is None or latest_local < slot:
                    if cadence != "every_two_days":
                        return slot
                    if latest_local is None or latest_local <= slot - timedelta(days=2):
                        return slot
            else:
                if cadence != "every_two_days":
                    return slot
                if latest_local is None:
                    return slot
                next_allowed = latest_local + timedelta(days=2)
                if slot >= next_allowed:
                    return slot
        check_day += timedelta(days=1)
    return datetime.combine(now.date() + timedelta(days=7), time(hour=20), tzinfo=LOCAL_TZ)


def cadence_allows_send(cadence: str, latest: datetime | None, now: datetime | None = None) -> bool:
    if cadence == "never":
        return False
    now = now or local_now()
    slot = next_reminder_slot(cadence, latest, now)
    return slot <= now


def reminder_schedule_meta(session: Session, template_key: str, latest: MessageLog | None, lease_id: int | None = None) -> dict[str, Any]:
    cadence = reminder_cadence(session, template_key, lease_id)
    if cadence == "never":
        return {
            "cadence": cadence,
            "cadence_label": cadence_label(cadence),
            "next_auto_at": None,
        }
    next_slot = next_reminder_slot(cadence, latest.created_at if latest else None)
    return {
        "cadence": cadence,
        "cadence_label": cadence_label(cadence),
        "next_auto_at": next_slot.isoformat(),
    }


def message_template(session: Session, key: str) -> str:
    return get_setting_value(session, key)


def get_tenant_links(session: Session) -> dict[str, str]:
    raw = get_setting_value(session, "telegram_tenant_links") or "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): str(value) for key, value in parsed.items() if value}


def save_tenant_links(session: Session, links: dict[str, str]) -> None:
    setting = session.get(AppSetting, "telegram_tenant_links")
    if not setting:
        setting = AppSetting(key="telegram_tenant_links")
        session.add(setting)
    setting.value = json.dumps(links, ensure_ascii=False)


def lease_chat_id(session: Session, lease: Lease) -> str:
    links = get_tenant_links(session)
    return links.get(str(lease.tenant_id), "")


def tenant_chat_linked(session: Session, lease: Lease) -> bool:
    return bool(lease_chat_id(session, lease))


def normalize_apartment_code(value: str) -> str:
    return re.sub(r"[^a-zа-я0-9]+", "", (value or "").lower().replace("ё", "е"))


def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D+", "", value or "")
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 10:
        digits = "7" + digits
    return digits


def lease_by_chat_id(session: Session, chat_id: int | str | None) -> Lease | None:
    if not chat_id:
        return None
    chat_ref = str(chat_id)
    links = get_tenant_links(session)
    for lease in session.scalars(select(Lease).where(Lease.active.is_(True))).all():
        if links.get(str(lease.tenant_id)) == chat_ref:
            return lease
    return None


def maybe_link_tenant_chat(session: Session, message: dict[str, Any]) -> Lease | None:
    sender = message.get("from") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    linked = lease_by_chat_id(session, chat_id)
    if linked:
        return linked
    contact = message.get("contact") or {}
    contact_phone = normalize_phone(contact.get("phone_number") or "")
    contact_user_id = str(contact.get("user_id") or "")
    sender_user_id = str(sender.get("id") or "")
    if chat_id and contact_phone and contact_user_id and contact_user_id == sender_user_id:
        for lease in session.scalars(select(Lease).where(Lease.active.is_(True))).all():
            if normalize_phone(lease.tenant.phone) == contact_phone:
                links = get_tenant_links(session)
                links[str(lease.tenant_id)] = str(chat_id)
                save_tenant_links(session, links)
                return lease
    handles = {
        normalize_telegram_handle(sender.get("username") or ""),
        normalize_telegram_handle(chat.get("username") or ""),
    }
    handles.discard("")
    if not chat_id or not handles:
        return None

    for lease in session.scalars(select(Lease).where(Lease.active.is_(True))).all():
        if normalize_telegram_handle(lease.tenant.telegram) in handles:
            links = get_tenant_links(session)
            links[str(lease.tenant_id)] = str(chat_id)
            save_tenant_links(session, links)
            return lease
    return None


def find_lease_by_receipt_purpose(session: Session, purpose: str) -> Lease | None:
    normalized = normalize_apartment_code(purpose)
    if not normalized:
        return None
    for lease in session.scalars(select(Lease).where(Lease.active.is_(True))).all():
        apartment_code = normalize_apartment_code(lease.apartment.name)
        if apartment_code and apartment_code in normalized:
            return lease
    return None


def fill_template(template: str, context: dict[str, Any]) -> str:
    text = template or ""
    for key, value in context.items():
        text = text.replace("{" + key + "}", str(value if value is not None else ""))
    return text


def reminder_label(template_key: str) -> str:
    return {
        "message_rent_due": "аренда сегодня",
        "message_rent_overdue": "долг по аренде",
        "message_utility_bill": "коммуналка",
        "message_all_debts": "все долги",
        "custom": "свой текст",
    }.get(template_key, template_key or "сообщение")


def serialize_message_log(log: MessageLog | None) -> dict[str, Any] | None:
    if not log:
        return None
    return {
        "id": log.id,
        "template_key": log.template_key,
        "label": reminder_label(log.template_key),
        "status": log.status,
        "created_at": log.created_at.isoformat(),
        "note": log.note,
    }


def latest_message_log(
    session: Session | None,
    *,
    rent_charge_id: int | None = None,
    utility_line_id: int | None = None,
) -> MessageLog | None:
    if not session:
        return None
    query = select(MessageLog)
    if rent_charge_id is not None:
        query = query.where(MessageLog.rent_charge_id == rent_charge_id)
    if utility_line_id is not None:
        query = query.where(MessageLog.utility_line_id == utility_line_id)
    return session.scalar(query.order_by(MessageLog.created_at.desc()).limit(1))


def reminder_meta(
    session: Session | None,
    lease: Lease,
    due_date: date | None,
    *,
    template_key: str | None = None,
    rent_charge_id: int | None = None,
    utility_line_id: int | None = None,
) -> dict[str, Any]:
    latest = latest_message_log(session, rent_charge_id=rent_charge_id, utility_line_id=utility_line_id)
    chat_id = lease_chat_id(session, lease) if session else ""
    cutoff = notification_cutoff_date(session) if session else date.today()
    auto_enabled = notifications_enabled(session) if session else False
    due = due_date or date.today()
    eligible = bool(chat_id) and due >= cutoff
    block_reason = ""
    if not chat_id:
        block_reason = "ждём /start"
    elif due < cutoff:
        block_reason = "старый долг до запуска"
    elif not auto_enabled:
        block_reason = "авто выключено"
    schedule = reminder_schedule_meta(session, template_key, latest, lease.id) if session and template_key else None
    return {
        "linked": bool(chat_id),
        "auto_enabled": auto_enabled,
        "eligible_auto": eligible and auto_enabled,
        "cutoff_date": cutoff.isoformat(),
        "block_reason": block_reason,
        "latest": serialize_message_log(latest),
        "schedule": schedule,
    }


def serialize_object(obj: RentalObject) -> dict[str, Any]:
    return {
        "id": obj.id,
        "name": obj.name,
        "short_code": obj.short_code,
        "notes": obj.notes,
        "active": obj.active,
        "apartments": [serialize_apartment(apartment) for apartment in sorted(obj.apartments, key=lambda item: item.sort_order)],
        "services": [serialize_service(service) for service in obj.services if service.active],
    }


def current_active_lease(apartment: Apartment, today: date | None = None) -> Lease | None:
    today = today or date.today()
    leases = sorted(apartment.leases, key=lambda item: item.start_date, reverse=True)
    return next(
        (
            lease
            for lease in leases
            if lease.active and lease.start_date <= today and (lease.end_date is None or lease.end_date >= today)
            and IGNORE_LEASE_MARK not in (lease.notes or "")
        ),
        None,
    )


def serialize_apartment(apartment: Apartment) -> dict[str, Any]:
    active_lease = current_active_lease(apartment)
    return {
        "id": apartment.id,
        "object_id": apartment.object_id,
        "object_name": apartment.object.name if apartment.object else "",
        "name": apartment.name,
        "sort_order": apartment.sort_order,
        "odn_share_percent": apartment.odn_share_percent,
        "active": apartment.active,
        "active_lease_id": active_lease.id if active_lease else None,
        "active_tenant": active_lease.tenant.full_name if active_lease else "",
    }


def serialize_tenant(tenant: Tenant) -> dict[str, Any]:
    return {
        "id": tenant.id,
        "full_name": tenant.full_name,
        "phone": tenant.phone,
        "telegram": tenant.telegram,
        "whatsapp": tenant.whatsapp,
        "notes": tenant.notes,
        "active": tenant.active,
    }


def serialize_lease(lease: Lease, session: Session | None = None) -> dict[str, Any]:
    return {
        "id": lease.id,
        "apartment_id": lease.apartment_id,
        "apartment": lease.apartment.name,
        "object": lease.apartment.object.name,
        "apartment_active": lease.apartment.active,
        "tenant_id": lease.tenant_id,
        "tenant": lease.tenant.full_name,
        "phone": lease.tenant.phone,
        "telegram": lease.tenant.telegram,
        "whatsapp": lease.tenant.whatsapp,
        "start_date": lease.start_date.isoformat(),
        "end_date": lease.end_date.isoformat() if lease.end_date else None,
        "payment_day": lease.payment_day,
        "ip_amount": lease.ip_amount,
        "personal_amount": lease.personal_amount,
        "deposit_amount": lease.deposit_amount,
        "deposit_location": lease.deposit_location,
        "deposit_terms": lease.deposit_terms,
        "notes": "\n".join(line for line in (lease.notes or "").splitlines() if line.strip() != IGNORE_LEASE_MARK),
        "active": lease.active,
        "ignored": lease_ignored(session, lease.id),
        "automation": get_lease_automation(session, lease.id) if session else {},
    }


def parse_receipt_details(receipt: PaymentReceipt) -> dict[str, Any]:
    raw = receipt.recipient_details or ""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def receipt_meta(parsed: dict[str, Any]) -> dict[str, Any]:
    meta = parsed.get("_meta") if isinstance(parsed, dict) else None
    return meta if isinstance(meta, dict) else {}


def payment_source_label(receipt: PaymentReceipt) -> str:
    if receipt.source == "manual":
        base = "ручной"
    elif receipt.channel == "ip":
        base = "ИП"
    elif receipt.channel == "personal":
        base = "перевод"
    elif receipt.channel == "expense_fund":
        base = "на расходы"
    elif receipt.channel == "utilities":
        base = "коммуналка"
    else:
        base = receipt.source or "платёж"
    return f"{base} от {receipt.created_at:%d.%m.%Y}"


def receipt_sender_name(parsed: dict[str, Any]) -> str:
    value = (parsed.get("payer_name") or "").strip()
    return value or "не указан"


def serialize_payment_receipt(receipt: PaymentReceipt, session: Session) -> dict[str, Any]:
    parsed = parse_receipt_details(receipt)
    charge = receipt.rent_charge
    utility_line = session.get(UtilityBillLine, receipt.utility_line_id) if receipt.utility_line_id else None
    lease = charge.lease if charge else utility_line.lease if utility_line else None
    apartment = lease.apartment.name if lease else ""
    tenant = lease.tenant.full_name if lease else ""
    target_label = ""
    target_month = ""
    if charge:
        target_label = f"аренда {format_date(charge.due_date)}"
        target_month = charge.due_date.isoformat()
    elif utility_line:
        target_label = f"коммуналка {format_date(utility_line.bill.period_end)}"
        target_month = utility_line.bill.period_end.isoformat()
    target_year = charge.due_date.year if charge else utility_line.bill.period_end.year if utility_line else date.today().year
    target_month_number = charge.due_date.month if charge else utility_line.bill.period_end.month if utility_line else 0
    return {
        "id": receipt.id,
        "lease_id": receipt.lease_id,
        "rent_charge_id": receipt.rent_charge_id,
        "utility_line_id": receipt.utility_line_id,
        "apartment": apartment,
        "tenant": tenant,
        "amount": money(receipt.amount),
        "channel": receipt.channel,
        "channel_label": receipt_channel_label(receipt.channel),
        "status": receipt.status,
        "paid_at": receipt.paid_at.isoformat(),
        "source": receipt.source,
        "source_label": payment_source_label(receipt),
        "created_at": receipt.created_at.isoformat(),
        "recipient_name": receipt.recipient_name,
        "sender_name": receipt_sender_name(parsed),
        "notes": receipt.notes or "",
        "file_path": receipt.file_path or "",
        "target_label": target_label,
        "target_month": target_month,
        "target_month_number": target_month_number,
        "target_year": target_year,
        "parsed": parsed,
    }


def lease_payment_target_options(session: Session, lease: Lease) -> dict[str, list[dict[str, Any]]]:
    rent_charges = session.scalars(
        select(RentCharge)
        .where(RentCharge.lease_id == lease.id, RentCharge.due_date >= RENT_GENERATION_START)
        .order_by(RentCharge.due_date.desc(), RentCharge.id.desc())
    ).all()
    utility_lines = session.scalars(
        select(UtilityBillLine)
        .where(UtilityBillLine.lease_id == lease.id)
        .order_by(UtilityBillLine.id.desc())
    ).all()
    return {
        "rent": [
            {
                "id": charge.id,
                "label": f"аренда {format_date(charge.due_date)}",
                "month": charge.due_date.month,
                "year": charge.due_date.year,
                "due_date": charge.due_date.isoformat(),
                "debt": money(max(0.0, charge.ip_due - charge.ip_paid) + max(0.0, charge.personal_due - charge.personal_paid)),
            }
            for charge in rent_charges
        ],
        "utility": [
            {
                "id": line.id,
                "label": f"ком. услуги {line.bill.period_start:%d.%m.%Y} -> {line.bill.period_end:%d.%m.%Y}",
                "period_start": line.bill.period_start.isoformat(),
                "period_end": line.bill.period_end.isoformat(),
                "debt": money(max(0.0, line.total_amount - line.paid_amount)),
                "status": line.status,
                "service": line.bill.service.name,
            }
            for line in utility_lines
        ],
    }


def serialize_rent_charge(charge: RentCharge, session: Session | None = None) -> dict[str, Any]:
    update_rent_charge_status(charge)
    reminder_template_key = "message_rent_due" if charge.status == "pending" and charge.due_date == date.today() else "message_rent_overdue"
    deferral_days_left = None
    if charge.deferral_until:
        deferral_days_left = max((charge.deferral_until - date.today()).days, 0)
    return {
        "id": charge.id,
        "lease_id": charge.lease_id,
        "object": charge.lease.apartment.object.name,
        "apartment": charge.lease.apartment.name,
        "tenant": charge.lease.tenant.full_name,
        "phone": charge.lease.tenant.phone,
        "telegram": charge.lease.tenant.telegram,
        "whatsapp": charge.lease.tenant.whatsapp,
        "period_start": charge.period_start.isoformat(),
        "period_end": charge.period_end.isoformat(),
        "due_date": charge.due_date.isoformat(),
        "ip_due": charge.ip_due,
        "personal_due": charge.personal_due,
        "ip_paid": charge.ip_paid,
        "personal_paid": charge.personal_paid,
        "total_due": money(charge.ip_due + charge.personal_due),
        "total_paid": money(charge.ip_paid + charge.personal_paid),
        "debt": money(max(0, charge.ip_due - charge.ip_paid) + max(0, charge.personal_due - charge.personal_paid)),
        "status": charge.status,
        "ip_status": status_for_amount(charge.ip_due, charge.ip_paid),
        "personal_status": status_for_amount(charge.personal_due, charge.personal_paid),
        "deferral_until": charge.deferral_until.isoformat() if charge.deferral_until else None,
        "deferral_days_left": deferral_days_left,
        "deferral_note": charge.deferral_note,
        "reminder": reminder_meta(
            session,
            charge.lease,
            charge.due_date,
            template_key=reminder_template_key,
            rent_charge_id=charge.id,
        ),
    }


def serialize_service(service: UtilityService) -> dict[str, Any]:
    return {
        "id": service.id,
        "object_id": service.object_id,
        "object": service.object.name if service.object else "",
        "kind": service.kind,
        "name": service.name,
        "provider_due_day": service.provider_due_day,
        "resident_due_days": service.resident_due_days,
        "active": service.active,
    }


def serialize_meter(meter: Meter) -> dict[str, Any]:
    latest = max(meter.readings, key=lambda item: item.reading_date) if meter.readings else None
    return {
        "id": meter.id,
        "service_id": meter.service_id,
        "service": meter.service.name,
        "object_id": meter.object_id,
        "object": meter.service.object.name,
        "apartment_id": meter.apartment_id,
        "apartment": meter.apartment.name if meter.apartment else "",
        "scope": meter.scope,
        "name": meter.name,
        "latest_date": latest.reading_date.isoformat() if latest else None,
        "latest_value": latest.value if latest else None,
        "active": meter.active,
    }


def serialize_bill_line(line: UtilityBillLine, session: Session | None = None) -> dict[str, Any]:
    update_utility_line_status(line)
    return {
        "id": line.id,
        "bill_id": line.bill_id,
        "apartment_id": line.apartment_id,
        "apartment": line.apartment.name,
        "object": line.bill.service.object.name,
        "service": line.bill.service.name,
        "lease_id": line.lease_id,
        "tenant": line.lease.tenant.full_name if line.lease else "",
        "personal_consumption": line.personal_consumption,
        "odn_consumption": line.odn_consumption,
        "total_amount": line.total_amount,
        "paid_amount": line.paid_amount,
        "debt": money(max(0, line.total_amount - line.paid_amount)),
        "status": line.status,
        "due_date": line.due_date.isoformat() if line.due_date else None,
        "note": line.note,
        "period_label": line.note,
        "bill_period_start": line.bill.period_start.isoformat(),
        "bill_period_end": line.bill.period_end.isoformat(),
        "bill_period_label": f"{line.bill.period_start:%d.%m.%Y} -> {line.bill.period_end:%d.%m.%Y} ({(line.bill.period_end - line.bill.period_start).days} дн.)",
        "reminder": reminder_meta(
            session,
            line.lease,
            line.due_date,
            template_key="message_utility_bill",
            utility_line_id=line.id,
        ) if line.lease else None,
    }


def serialize_bill(bill: UtilityBill, session: Session | None = None) -> dict[str, Any]:
    resident_total_amount = money(sum(line.total_amount for line in bill.lines))
    resident_paid_amount = money(sum(line.paid_amount for line in bill.lines))
    return {
        "id": bill.id,
        "service_id": bill.service_id,
        "service": bill.service.name,
        "object": bill.service.object.name,
        "period_start": bill.period_start.isoformat(),
        "period_end": bill.period_end.isoformat(),
        "period_label": f"{bill.period_start:%d.%m.%Y} -> {bill.period_end:%d.%m.%Y} ({(bill.period_end - bill.period_start).days} дн.)",
        "days": (bill.period_end - bill.period_start).days,
        "status": bill.status,
        "total_consumption": bill.total_consumption,
        "apartment_consumption": bill.apartment_consumption,
        "odn_consumption": bill.odn_consumption,
        "total_cost": bill.total_cost,
        "resident_total_amount": resident_total_amount,
        "resident_paid_amount": resident_paid_amount,
        "average_unit_price": bill.average_unit_price,
        "due_date": bill.due_date.isoformat() if bill.due_date else None,
        "is_forecast": bill.is_forecast,
        "provider_paid": bill.provider_paid,
        "provider_paid_at": bill.provider_paid_at.isoformat() if bill.provider_paid_at else None,
        "notes": bill.notes,
        "lines": [serialize_bill_line(line, session) for line in bill.lines],
    }


def serialize_expense(expense: Expense) -> dict[str, Any]:
    return {
        "id": expense.id,
        "expense_date": expense.expense_date.isoformat(),
        "object_id": expense.object_id,
        "object": expense.object.name if expense.object else "",
        "apartment_id": expense.apartment_id,
        "apartment": expense.apartment.name if expense.apartment else "",
        "category": expense.category,
        "amount": expense.amount,
        "source_funds": expense.source_funds,
        "payment_method": expense.payment_method,
        "description": expense.description,
        "compensation_status": expense.compensation_status,
        "compensated_at": expense.compensated_at.isoformat() if expense.compensated_at else None,
        "file_path": expense.file_path,
        "notes": expense.notes,
    }


def expense_fund_receipt_marker(receipt_id: int) -> str:
    return f"{EXPENSE_FUND_RECEIPT_MARK}{receipt_id}"


def linked_expense_fund_record(session: Session, receipt_id: int) -> Expense | None:
    marker = expense_fund_receipt_marker(receipt_id)
    return session.scalar(select(Expense).where(Expense.notes.contains(marker)).limit(1))


def sync_expense_fund_receipt(session: Session, receipt: PaymentReceipt) -> None:
    existing = linked_expense_fund_record(session, receipt.id)
    eligible = (
        receipt.status == "accepted"
        and receipt.channel == "expense_fund"
        and receipt.rent_charge_id is not None
        and receipt.lease_id is not None
    )
    if not eligible:
        if existing:
            session.delete(existing)
        return

    lease = receipt.rent_charge.lease if receipt.rent_charge else session.get(Lease, receipt.lease_id)
    if not lease:
        if existing:
            session.delete(existing)
        return

    marker = expense_fund_receipt_marker(receipt.id)
    expense = existing or Expense(notes=marker)
    expense.expense_date = receipt.paid_at.date()
    expense.object_id = lease.apartment.object_id
    expense.apartment_id = lease.apartment_id
    expense.category = "Пополнение на расходы"
    expense.amount = money(receipt.amount)
    expense.source_funds = "expense_fund_income"
    expense.payment_method = receipt.source or "manual"
    expense.description = f"Пополнение на расходы от {lease.tenant.full_name}"
    expense.compensation_status = "not_required"
    expense.file_path = receipt.file_path or ""
    if marker not in (expense.notes or ""):
        expense.notes = "; ".join(part for part in [expense.notes, marker] if part)
    session.add(expense)


def sync_expense_fund_receipts(session: Session, receipts: list[PaymentReceipt]) -> None:
    for receipt in receipts:
        sync_expense_fund_receipt(session, receipt)


def build_object_summary(session: Session) -> dict[str, Any]:
    apartments = session.scalars(select(Apartment).where(Apartment.active.is_(True))).all()
    occupied = [apartment for apartment in apartments if current_active_lease(apartment)]
    by_object = []
    for rental_object in session.scalars(select(RentalObject).order_by(RentalObject.name)).all():
        active_apartments = [apartment for apartment in rental_object.apartments if apartment.active]
        occupied_apartments = [apartment for apartment in active_apartments if current_active_lease(apartment)]
        by_object.append(
            {
                "object": rental_object.name,
                "occupied": len(occupied_apartments),
                "total": len(active_apartments),
            }
        )
    return {
        "occupied": len(occupied),
        "total": len(apartments),
        "by_object": by_object,
    }


@app.get("/api/bootstrap")
def bootstrap(session: Session = Depends(get_session)) -> dict[str, Any]:
    generate_rent_charges(session)
    session.commit()
    return {
        "today": date.today().isoformat(),
        "objects": [serialize_object(obj) for obj in session.scalars(select(RentalObject).order_by(RentalObject.name)).all()],
        "leases": [serialize_lease(lease, session) for lease in session.scalars(select(Lease).order_by(Lease.active.desc(), Lease.start_date.desc())).all()],
        "meters": [serialize_meter(meter) for meter in session.scalars(select(Meter).order_by(Meter.object_id, Meter.scope, Meter.name)).all()],
        "services": [serialize_service(service) for service in session.scalars(select(UtilityService).order_by(UtilityService.object_id, UtilityService.kind)).all()],
        "settings": get_settings(session),
        "dashboard": build_dashboard(session),
    }


@app.get("/api/settings")
def api_get_settings(session: Session = Depends(get_session)) -> dict[str, str | bool]:
    return get_settings(session)


@app.post("/api/settings")
def api_save_settings(payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, str | bool]:
    return save_settings(session, payload)


@app.get("/api/leases/{lease_id}/cadence")
def api_get_lease_cadence(lease_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    lease = session.get(Lease, lease_id)
    if not lease:
        raise HTTPException(404, "Договор не найден")
    return {"automation": get_lease_automation(session, lease_id)}


@app.post("/api/leases/{lease_id}/cadence")
def api_set_lease_cadence(lease_id: int, payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    lease = session.get(Lease, lease_id)
    if not lease:
        raise HTTPException(404, "Договор не найден")
    automation = save_lease_automation(session, lease_id, payload)
    session.commit()
    return {"automation": automation}


@app.delete("/api/leases/{lease_id}/cadence")
def api_clear_lease_cadence(lease_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    lease = session.get(Lease, lease_id)
    if not lease:
        raise HTTPException(404, "Договор не найден")
    for template_key in LEASE_AUTOMATION_TEMPLATES:
        clear_lease_cadence(session, lease_id, template_key)
    session.commit()
    return {"automation": get_lease_automation(session, lease_id)}


@app.patch("/api/leases/{lease_id}/automation")
def api_update_lease_automation(lease_id: int, payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    lease = session.get(Lease, lease_id)
    if not lease:
        raise HTTPException(404, "Договор не найден")
    automation = save_lease_automation(session, lease_id, payload)
    session.commit()
    return {"automation": automation}


@app.patch("/api/leases/{lease_id}/ignore")
def api_update_lease_ignore(lease_id: int, payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    lease = session.get(Lease, lease_id)
    if not lease:
        raise HTTPException(404, "Договор не найден")
    set_lease_ignored(session, lease_id, setting_bool_value(payload.get("ignored")))
    session.commit()
    return serialize_lease(lease, session)


def import_release_baseline(session: Session) -> dict[str, int]:
    for model in [MessageLog, PaymentReceipt, UtilityBillLine, UtilityBill, RentCharge, Lease, Tenant, Expense, MeterReading, Tariff]:
        session.execute(delete(model))
    session.flush()
    seed_if_empty(session)
    scripts_dir = ROOT_DIR / "scripts"
    import sys

    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import seed_from_screenshots as legacy_seed  # type: ignore

    legacy_seed.seed_tariffs(session)
    leases = legacy_seed.seed_leases(session)
    legacy_seed.generate_rent_charges(session, until=date(2026, 5, 31))
    legacy_seed.mark_old_rent_paid(session)
    legacy_seed.seed_rent_payments(session, leases)
    legacy_seed.seed_utility_payments(session, leases)
    legacy_seed.seed_meter_readings(session)
    legacy_seed.seed_expenses(session)
    session.flush()
    return {
        "tenants": session.scalar(select(func.count(Tenant.id))) or 0,
        "leases": session.scalar(select(func.count(Lease.id))) or 0,
        "receipts": session.scalar(select(func.count(PaymentReceipt.id))) or 0,
        "readings": session.scalar(select(func.count(MeterReading.id))) or 0,
    }


@app.post("/api/admin/import-release-baseline")
def api_import_release_baseline(session: Session = Depends(get_session)) -> dict[str, int]:
    result = import_release_baseline(session)
    session.commit()
    return result


def add_report_issue(issues: list[dict[str, Any]], severity: str, title: str, count: int = 1, detail: str = "") -> None:
    if count <= 0:
        return
    issues.append(
        {
            "severity": severity,
            "title": title,
            "count": count,
            "detail": detail,
        }
    )


def utility_bill_for_month(session: Session, service: UtilityService, start: date, end: date) -> UtilityBill | None:
    next_day = end + timedelta(days=1)
    return session.scalar(
        select(UtilityBill)
        .where(
            UtilityBill.service_id == service.id,
            UtilityBill.period_start >= start,
            UtilityBill.period_start < next_day,
        )
        .order_by(UtilityBill.period_start.desc())
        .limit(1)
    )


def object_reading_in_month(session: Session, service: UtilityService, start: date, end: date) -> MeterReading | None:
    meter = session.scalar(
        select(Meter)
        .where(Meter.service_id == service.id, Meter.scope == "object")
        .limit(1)
    )
    if not meter:
        return None
    return session.scalar(
        select(MeterReading)
        .where(
            MeterReading.meter_id == meter.id,
            MeterReading.reading_date >= start,
            MeterReading.reading_date <= end,
        )
        .order_by(MeterReading.reading_date.desc())
        .limit(1)
    )


def monthly_report_status(session: Session, year: int, month: int, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    start, end = month_range(year, month)
    issues: list[dict[str, Any]] = []

    rent_charges = session.scalars(
        select(RentCharge)
        .join(Lease)
        .join(Apartment)
        .where(Apartment.active.is_(True))
        .where(RentCharge.due_date >= start, RentCharge.due_date <= end)
        .order_by(RentCharge.due_date)
    ).all()
    for charge in rent_charges:
        update_rent_charge_status(charge, today)
    critical_rent = [charge for charge in rent_charges if charge.status in {"overdue", "pending", "partial"}]
    deferred_rent = [charge for charge in rent_charges if charge.status == "deferred"]
    add_report_issue(issues, "danger", "не закрыта аренда", len(critical_rent), "Есть платежи аренды без полного закрытия.")
    add_report_issue(issues, "warn", "есть отсрочки по аренде", len(deferred_rent), "Отсрочка не пожар, но забыть её легко. А зачем?")

    utility_lines = session.scalars(
        select(UtilityBillLine)
        .join(UtilityBill)
        .join(Apartment, UtilityBillLine.apartment_id == Apartment.id)
        .where(
            Apartment.active.is_(True),
            UtilityBillLine.due_date >= start,
            UtilityBillLine.due_date <= end,
        )
    ).all()
    for line in utility_lines:
        update_utility_line_status(line, today)
    tenant_utility_debts = [line for line in utility_lines if line.status in {"overdue", "partial", "issued", "pending"}]
    add_report_issue(issues, "warn", "долги жильцов за коммуналку", len(tenant_utility_debts), "Коммуналка жильцов не закрыта полностью.")

    missing_bills = 0
    unpaid_provider_bills = 0
    missing_readings = 0
    for service in session.scalars(select(UtilityService).where(UtilityService.active.is_(True))).all():
        bill = utility_bill_for_month(session, service, start, end)
        if not bill:
            missing_bills += 1
        elif not bill.provider_paid:
            unpaid_provider_bills += 1
        if not object_reading_in_month(session, service, start, end):
            missing_readings += 1

    add_report_issue(issues, "danger", "нет коммунального счёта", missing_bills, "По услуге не создан месячный счёт.")
    add_report_issue(issues, "danger", "поставщик не оплачен", unpaid_provider_bills, "Деньги поставщику не отмечены как оплаченные.")
    add_report_issue(issues, "danger", "нет общих показаний", missing_readings, "За месяц не найдены общедомовые показания.")

    suspicious_receipts = session.scalar(
        select(PaymentReceipt.id)
        .where(PaymentReceipt.status == "suspicious", PaymentReceipt.created_at >= datetime.combine(start, datetime.min.time()))
        .limit(1)
    )
    add_report_issue(issues, "danger", "есть подозрительные чеки", 1 if suspicious_receipts else 0, "Проверить получателя и назначение платежа.")

    warning_count = sum(issue["count"] for issue in issues if issue["severity"] == "warn")
    danger_count = sum(issue["count"] for issue in issues if issue["severity"] == "danger")
    total_issues = warning_count + danger_count
    if danger_count >= 5 or total_issues >= 10:
        severity = "critical"
        label = "много проблем"
    elif danger_count:
        severity = "danger"
        label = "критические проблемы"
    elif warning_count:
        severity = "warn"
        label = "есть вопросы"
    else:
        severity = "ok"
        label = "закрыт"

    return {
        "year": year,
        "month": month,
        "month_name": MONTH_NAMES[month - 1],
        "title": f"Готов отчёт за {MONTH_NAMES[month - 1]} {year}",
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "severity": severity,
        "label": label,
        "issue_count": total_issues,
        "warning_count": warning_count,
        "danger_count": danger_count,
        "issues": issues,
        "download_url": f"/api/reports/monthly.xlsx?year={year}&month={month}",
    }


def build_monthly_reports(session: Session, today: date | None = None) -> list[dict[str, Any]]:
    reports = [monthly_report_status(session, year, month, today) for year, month in iter_generated_report_months(today)]
    return [report for report in reports if report["severity"] != "ok"]


def build_dashboard(session: Session) -> dict[str, Any]:
    today = date.today()
    cutoff = configured_notification_cutoff_date(session)
    charges = session.scalars(
        select(RentCharge)
        .join(Lease)
        .join(Apartment)
        .where(Lease.active.is_(True), Apartment.active.is_(True))
        .order_by(RentCharge.due_date)
    ).all()
    charges = [charge for charge in charges if not lease_ignored(session, charge.lease_id)]
    for charge in charges:
        update_rent_charge_status(charge, today)
    utility_lines = session.scalars(select(UtilityBillLine).join(Apartment).where(Apartment.active.is_(True))).all()
    utility_lines = [line for line in utility_lines if line.lease_id and not lease_ignored(session, line.lease_id)]
    for line in utility_lines:
        update_utility_line_status(line, today)

    def rent_debt(charge: RentCharge) -> float:
        return money(max(0, float(charge.ip_due or 0) - float(charge.ip_paid or 0)) + max(0, float(charge.personal_due or 0) - float(charge.personal_paid or 0)))

    def utility_debt(line: UtilityBillLine) -> float:
        return money(max(0, float(line.total_amount or 0) - float(line.paid_amount or 0)))

    overdue_rent = [
        serialize_rent_charge(charge, session)
        for charge in charges
        if charge.status == "overdue" and rent_debt(charge) > 0 and debt_visible_by_cutoff(charge.due_date, cutoff)
    ]
    partial_rent = [
        serialize_rent_charge(charge, session)
        for charge in charges
        if charge.status == "partial" and rent_debt(charge) > 0 and debt_visible_by_cutoff(charge.due_date, cutoff)
    ]
    today_rent = [
        serialize_rent_charge(charge, session)
        for charge in charges
        if charge.due_date == today and charge.status not in {"paid", "paid_ahead"} and rent_debt(charge) > 0 and debt_visible_by_cutoff(charge.due_date, cutoff)
    ]
    deferred = [
        serialize_rent_charge(charge, session)
        for charge in charges
        if charge.status == "deferred"
        and charge.deferral_until
        and charge.deferral_until <= today + timedelta(days=2)
        and rent_debt(charge) > 0
        and debt_visible_by_cutoff(charge.due_date, cutoff)
    ]

    overdue_utilities = [
        serialize_bill_line(line, session)
        for line in utility_lines
        if line.status == "overdue" and utility_debt(line) > 0 and debt_visible_by_cutoff(line.due_date, cutoff)
    ]
    partial_utilities = [
        serialize_bill_line(line, session)
        for line in utility_lines
        if line.status == "partial" and utility_debt(line) > 0 and debt_visible_by_cutoff(line.due_date, cutoff)
    ]
    issued_utilities = [
        serialize_bill_line(line, session)
        for line in utility_lines
        if line.status == "issued" and utility_debt(line) > 0 and debt_visible_by_cutoff(line.due_date, cutoff)
    ]

    pending_expenses = session.scalars(
        select(Expense).where(Expense.source_funds == "personal", Expense.compensation_status != "compensated")
    ).all()
    expense_fund_received = money(
        session.scalar(select(func.coalesce(func.sum(Expense.amount), 0)).where(Expense.source_funds == "expense_fund_income")) or 0
    )
    expense_fund_spent = money(
        session.scalar(
            select(func.coalesce(func.sum(Expense.amount), 0)).where(
                Expense.source_funds != "expense_fund_income",
                Expense.compensation_status != "compensated",
            )
        )
        or 0
    )
    expense_fund_balance = money(expense_fund_received - expense_fund_spent)

    stale_readings = []
    for service in session.scalars(select(UtilityService).where(UtilityService.active.is_(True))).all():
        last_date = object_last_reading_date(session, service)
        if not last_date or (today - last_date).days >= 30:
            stale_readings.append(
                {
                    "service_id": service.id,
                    "object": service.object.name,
                    "service": service.name,
                    "last_date": last_date.isoformat() if last_date else None,
                    "days": (today - last_date).days if last_date else None,
                }
            )

    provider_debts = [
        serialize_bill(bill)
        for bill in session.scalars(select(UtilityBill).where(UtilityBill.provider_paid.is_(False))).all()
        if bill.status in {"issued", "draft"}
        and debt_visible_by_cutoff(bill.due_date or bill.period_end, cutoff)
        and any(line.status != "paid" for line in bill.lines)
    ]

    suspicious_receipts = session.scalars(select(PaymentReceipt).where(PaymentReceipt.status == "suspicious")).all()

    return {
        "object_summary": build_object_summary(session),
        "monthly_reports": build_monthly_reports(session, today),
        "rent_overdue": overdue_rent,
        "rent_partial": partial_rent,
        "rent_today": today_rent,
        "rent_deferred": deferred,
        "utility_overdue": overdue_utilities,
        "utility_partial": partial_utilities,
        "utility_issued": issued_utilities,
        "pending_personal_expenses": [serialize_expense(expense) for expense in pending_expenses],
        "expense_fund": {
            "received": expense_fund_received,
            "spent": expense_fund_spent,
            "balance": expense_fund_balance,
            "has_mismatch": expense_fund_balance > 0.009,
        },
        "stale_readings": stale_readings,
        "provider_debts": provider_debts,
        "suspicious_receipts": [serialize_payment_receipt(receipt, session) for receipt in suspicious_receipts],
    }


def money_text(value: float) -> str:
    return f"{money(value):,.2f}".replace(",", " ").replace(".", ",") + " ₽"


def first_open_rent_charge(lease: Lease, cutoff: date | None = None) -> RentCharge | None:
    charges = sorted(lease.rent_charges, key=lambda item: item.due_date)
    for charge in charges:
        update_rent_charge_status(charge)
        if not debt_visible_by_cutoff(charge.due_date, cutoff):
            continue
        if charge.status not in {"paid", "paid_ahead"}:
            return charge
    return None


def first_open_utility_line(session: Session, lease: Lease, cutoff: date | None = None) -> UtilityBillLine | None:
    lines = session.scalars(select(UtilityBillLine).where(UtilityBillLine.lease_id == lease.id)).all()
    lines = sorted(lines, key=lambda item: (item.due_date or date.max, item.id))
    for line in lines:
        update_utility_line_status(line)
        if not debt_visible_by_cutoff(line.due_date, cutoff):
            continue
        if line.status not in {"paid", "paid_ahead"}:
            return line
    return None


def month_title(value: date | None) -> str:
    if not value:
        return ""
    return f"{MONTH_NAMES[value.month - 1]} {value.year}"


def outstanding_rent_charges(lease: Lease, today: date | None = None, cutoff: date | None = None) -> list[RentCharge]:
    today = today or date.today()
    result: list[RentCharge] = []
    for charge in sorted(lease.rent_charges, key=lambda item: (item.due_date, item.id)):
        update_rent_charge_status(charge, today)
        debt = max(0.0, charge.ip_due - charge.ip_paid) + max(0.0, charge.personal_due - charge.personal_paid)
        if debt > 0.009 and charge.due_date <= today and debt_visible_by_cutoff(charge.due_date, cutoff):
            result.append(charge)
    return result


def outstanding_utility_lines(
    session: Session,
    lease: Lease,
    today: date | None = None,
    cutoff: date | None = None,
) -> list[UtilityBillLine]:
    today = today or date.today()
    result: list[UtilityBillLine] = []
    lines = session.scalars(select(UtilityBillLine).where(UtilityBillLine.lease_id == lease.id)).all()
    for line in sorted(lines, key=lambda item: (item.due_date or date.max, item.id)):
        update_utility_line_status(line, today)
        debt = max(0.0, line.total_amount - line.paid_amount)
        if debt > 0.009 and line.due_date and line.due_date <= today and debt_visible_by_cutoff(line.due_date, cutoff):
            result.append(line)
    return result


def issued_utility_lines(
    session: Session,
    lease: Lease,
    cutoff: date | None = None,
) -> list[UtilityBillLine]:
    result: list[UtilityBillLine] = []
    lines = session.scalars(select(UtilityBillLine).where(UtilityBillLine.lease_id == lease.id)).all()
    for line in sorted(lines, key=lambda item: (item.due_date or date.max, item.id)):
        update_utility_line_status(line)
        debt = max(0.0, line.total_amount - line.paid_amount)
        if debt > 0.009 and line.status == "issued" and debt_visible_by_cutoff(line.due_date, cutoff):
            result.append(line)
    return result


def debt_month_heading(value: date | None) -> str:
    if not value:
        return ""
    current_year = date.today().year
    month_name = MONTH_NAMES[value.month - 1].capitalize()
    return month_name if value.year == current_year else f"{month_name} {value.year}"


def rent_debt_bullet(charge: RentCharge) -> str:
    ip_left = money(max(0.0, charge.ip_due - charge.ip_paid))
    personal_left = money(max(0.0, charge.personal_due - charge.personal_paid))
    ip_paid = money(charge.ip_paid) > 0.009
    personal_paid = money(charge.personal_paid) > 0.009

    if ip_left <= 0.009 and personal_left <= 0.009:
        return "аренда закрыта."
    if ip_left > 0.009 and personal_left > 0.009:
        if not ip_paid and not personal_paid:
            return "аренда не оплачена. ИП не оплачено, перевод по номеру телефона не поступил."
        if ip_paid and not personal_paid:
            return f"неполная оплата аренды. По ИП есть оплата, не хватает {money_text(ip_left)}. Перевод по номеру телефона не поступил."
        if not ip_paid and personal_paid:
            return f"неполная оплата аренды. ИП не оплачено, по номеру телефона не хватает {money_text(personal_left)}."
        return f"неполная оплата аренды. По ИП не хватает {money_text(ip_left)}, по номеру телефона не хватает {money_text(personal_left)}."
    if ip_left > 0.009:
        if ip_paid:
            return f"неполная оплата аренды. По ИП не хватает {money_text(ip_left)}, перевод по номеру телефона оплачен полностью."
        return "неполная оплата аренды. ИП не оплачено, перевод по номеру телефона оплачен полностью."
    if personal_paid:
        return f"неполная оплата аренды. ИП оплачено, по номеру телефона не хватает {money_text(personal_left)}."
    return "неполная оплата аренды. ИП оплачено, перевод по номеру телефона не поступил."


def rent_channel_summary_text(charge: RentCharge) -> str:
    ip_done = money(charge.ip_paid) + 0.009 >= money(charge.ip_due)
    personal_done = money(charge.personal_paid) + 0.009 >= money(charge.personal_due)
    if ip_done and personal_done:
        return "всё оплачено"
    if ip_done and not personal_done:
        return "ИП оплачен, перевод нет"
    if not ip_done and personal_done:
        return "ИП нет, перевод оплачен"
    return "ИП нет, перевода нет"


def utility_debt_bullet(lines: list[UtilityBillLine]) -> str:
    total = money(sum(line.total_amount for line in lines))
    paid = money(sum(line.paid_amount for line in lines))
    debt = money(max(0.0, total - paid))
    if paid <= 0.009:
        return f"неоплачены счета по коммунальным платежам. Всего {money_text(total)}."
    return f"неоплачены счета по коммунальным платежам. Всего {money_text(total)} — оплачено {money_text(paid)}. Остаток {money_text(debt)}."


def utility_message_line(line: UtilityBillLine) -> str:
    debt = money(max(0.0, line.total_amount - line.paid_amount))
    service_name = (line.bill.service.name or "коммуналка").strip().lower()
    period = f"{line.bill.period_start:%d.%m.%Y} -> {line.bill.period_end:%d.%m.%Y}"
    return f"{line.bill.service.object.name}, {service_name} за период {period}: {money_text(debt)}"


def selected_utility_lines(
    session: Session,
    lease: Lease,
    line: UtilityBillLine | None = None,
    utility_lines_override: list[UtilityBillLine] | None = None,
    today: date | None = None,
) -> list[UtilityBillLine]:
    cutoff = configured_notification_cutoff_date(session)
    if utility_lines_override is not None:
        lines = [item for item in utility_lines_override if debt_visible_by_cutoff(item.due_date, cutoff)]
    else:
        lines = outstanding_utility_lines(session, lease, today, cutoff)
        if line and debt_visible_by_cutoff(line.due_date, cutoff) and all(existing.id != line.id for existing in lines):
            lines = [*lines, line]
    unique: dict[int, UtilityBillLine] = {}
    for item in sorted(lines, key=lambda current: (current.bill.period_end, current.id)):
        if max(0.0, item.total_amount - item.paid_amount) <= 0.009:
            continue
        unique[item.id] = item
    return list(unique.values())


def build_all_debts_breakdown(session: Session, lease: Lease, today: date | None = None) -> str:
    today = today or date.today()
    cutoff = configured_notification_cutoff_date(session)
    rent_debts = outstanding_rent_charges(lease, today, cutoff)
    utility_debts = outstanding_utility_lines(session, lease, today, cutoff)

    by_month: dict[tuple[int, int], list[str]] = {}
    for charge in rent_debts:
        key = (charge.due_date.year, charge.due_date.month)
        by_month.setdefault(key, []).append(f"- {rent_debt_bullet(charge)}")

    utility_groups: dict[tuple[int, int], list[UtilityBillLine]] = {}
    for line in utility_debts:
        key = (line.bill.period_end.year, line.bill.period_end.month)
        utility_groups.setdefault(key, []).append(line)

    for key, lines in utility_groups.items():
        by_month.setdefault(key, []).append(f"- {utility_debt_bullet(lines)}")

    if not by_month:
        return "Задолженностей на сегодня нет."

    parts: list[str] = []
    for year, month in sorted(by_month):
        parts.append(f"{debt_month_heading(date(year, month, 1))}:")
        parts.extend(by_month[(year, month)])
    return "\n".join(parts)


def build_message_context(
    session: Session,
    lease: Lease,
    charge: RentCharge | None = None,
    line: UtilityBillLine | None = None,
    custom_text: str = "",
    utility_lines_override: list[UtilityBillLine] | None = None,
    utility_due_date_override: date | None = None,
) -> dict[str, str]:
    today = date.today()
    cutoff = configured_notification_cutoff_date(session)
    charge = charge or first_open_rent_charge(lease, cutoff)
    line = line or first_open_utility_line(session, lease, cutoff)
    current_rent_debt = 0.0
    ip_due = 0.0
    personal_due = 0.0
    rent_due_date = ""
    rent_period = ""
    if charge:
        update_rent_charge_status(charge)
        ip_due = max(0.0, charge.ip_due - charge.ip_paid)
        personal_due = max(0.0, charge.personal_due - charge.personal_paid)
        current_rent_debt = ip_due + personal_due
        rent_due_date = format_date(charge.due_date)
        rent_period = f"{format_date(charge.period_start)} - {format_date(charge.period_end)}"

    utility_lines = selected_utility_lines(session, lease, line, utility_lines_override, today)
    current_utility_total = sum(max(0.0, item.total_amount - item.paid_amount) for item in utility_lines)
    utility_due_date_value = utility_due_date_override or next((item.due_date for item in utility_lines if item.due_date), line.due_date if line else None)
    utility_due_date = format_date(utility_due_date_value) if utility_due_date_value else ""
    utility_period = ""
    utility_service = ""
    if utility_lines:
        first_line = utility_lines[0]
        if len(utility_lines) == 1:
            utility_period = f"{format_date(first_line.bill.period_start)} - {format_date(first_line.bill.period_end)}"
            utility_service = first_line.bill.service.name
        else:
            utility_service = "коммунальные платежи"
            utility_period = "; ".join(
                f"{format_date(item.bill.period_start)} - {format_date(item.bill.period_end)}"
                for item in utility_lines
            )

    rent_debts = outstanding_rent_charges(lease, today, cutoff)
    utility_debts = outstanding_utility_lines(session, lease, today, cutoff)
    total_rent_debt = sum(max(0.0, item.ip_due - item.ip_paid) + max(0.0, item.personal_due - item.personal_paid) for item in rent_debts)
    total_utility_debt = sum(max(0.0, item.total_amount - item.paid_amount) for item in utility_debts)

    rent_months = [month_title(item.due_date) for item in rent_debts]
    utility_items = [utility_message_line(item) for item in utility_lines] or [
        f"{item.bill.service.name} за {month_title(item.bill.period_end)} — {money_text(max(0.0, item.total_amount - item.paid_amount))}"
        for item in utility_debts
    ]
    rent_details = [
        f"{month_title(item.due_date)} — {money_text(max(0.0, item.ip_due - item.ip_paid) + max(0.0, item.personal_due - item.personal_paid))}"
        for item in rent_debts
    ]

    rent_debt = total_rent_debt if total_rent_debt > 0 else current_rent_debt
    utility_total = total_utility_debt if total_utility_debt > 0 else current_utility_total

    return {
        "tenant_name": lease.tenant.full_name,
        "apartment": lease.apartment.name,
        "object": lease.apartment.object.name,
        "phone": lease.tenant.phone or "",
        "telegram": lease.tenant.telegram or "",
        "payment_day": str(lease.payment_day),
        "ip_due": money_text(ip_due),
        "personal_due": money_text(personal_due),
        "total_due": money_text(rent_debt),
        "debt": money_text(rent_debt),
        "rent_due_date": rent_due_date,
        "rent_period": rent_period,
        "utility_total": money_text(utility_total),
        "utility_due_date": utility_due_date,
        "period": utility_period or rent_period,
        "utility_service": utility_service,
        "rent_debt_total": money_text(total_rent_debt),
        "rent_debt_months_count": str(len(rent_months)),
        "rent_debt_months": ", ".join(rent_months),
        "rent_debt_months_details": "; ".join(rent_details),
        "utility_debt_total": money_text(total_utility_debt),
        "utility_debt_count": str(len(utility_items)),
        "utility_debt_details": "\n".join(utility_items),
        "all_debt_total": money_text(total_rent_debt + total_utility_debt),
        "all_debts_breakdown": build_all_debts_breakdown(session, lease, today),
        "custom_text": custom_text,
        "personal_recipient_phone_text": get_settings(session).get("personal_recipient_phone") or "+79133854441",
        "personal_recipient_bank_text": get_settings(session).get("personal_recipient_bank") or "Сбербанк",
    }


def render_message_text(
    session: Session,
    template_key: str,
    lease: Lease,
    charge: RentCharge | None = None,
    line: UtilityBillLine | None = None,
    custom_text: str = "",
    utility_lines_override: list[UtilityBillLine] | None = None,
    utility_due_date_override: date | None = None,
) -> str:
    if template_key == "custom":
        return custom_text.strip()
    context = build_message_context(
        session,
        lease,
        charge,
        line,
        custom_text,
        utility_lines_override=utility_lines_override,
        utility_due_date_override=utility_due_date_override,
    )
    template = message_template(session, template_key)
    if template_key == "message_rent_overdue" and "{rent_debt_months" not in template:
        template = (
            template.rstrip()
            + "\nДолги по аренде: {rent_debt_months_count} мес. ({rent_debt_months})."
            + "\nПо месяцам: {rent_debt_months_details}."
            + "\nКоммуналка: {utility_debt_total}. Общий долг: {all_debt_total}."
        )
    if template_key == "message_all_debts" and "{all_debts_breakdown}" not in template:
        template = template.rstrip() + "\n{all_debts_breakdown}"
    if template_key == "message_utility_bill" and "{utility_debt_details}" not in template:
        template = DEFAULT_SETTINGS["message_utility_bill"]
    return fill_template(template, context)


def serialize_message_target(session: Session, lease: Lease) -> dict[str, Any]:
    cutoff = configured_notification_cutoff_date(session)
    today = date.today()
    charge = first_open_rent_charge(lease, cutoff)
    line = first_open_utility_line(session, lease, cutoff)
    rent_issues = outstanding_rent_charges(lease, today, cutoff)
    utility_issues = [*outstanding_utility_lines(session, lease, today, cutoff), *issued_utility_lines(session, lease, cutoff)]
    rent_debt = money(max(0.0, (charge.ip_due - charge.ip_paid) if charge else 0.0) + max(0.0, (charge.personal_due - charge.personal_paid) if charge else 0.0))
    utility_debt = money(max(0.0, (line.total_amount - line.paid_amount) if line else 0.0))
    return {
        "lease_id": lease.id,
        "tenant_id": lease.tenant_id,
        "tenant": lease.tenant.full_name,
        "object": lease.apartment.object.name,
        "apartment": lease.apartment.name,
        "telegram": lease.tenant.telegram,
        "linked": tenant_chat_linked(session, lease),
        "automation": get_lease_automation(session, lease.id),
        "rent_charge_id": charge.id if charge else None,
        "rent_status": charge.status if charge else "",
        "rent_debt": rent_debt,
        "rent_reminder": reminder_meta(
            session,
            lease,
            charge.due_date,
            template_key="message_rent_overdue",
            rent_charge_id=charge.id,
        ) if charge else None,
        "utility_line_id": line.id if line else None,
        "utility_status": line.status if line else "",
        "utility_debt": utility_debt,
        "utility_reminder": reminder_meta(
            session,
            lease,
            line.due_date,
            template_key="message_utility_bill",
            utility_line_id=line.id,
        ) if line else None,
        "rent_items": [
            {
                "id": item.id,
                "status": item.status,
                "due_date": item.due_date.isoformat(),
                "label": debt_month_heading(item.due_date),
                "debt": money(max(0.0, item.ip_due - item.ip_paid) + max(0.0, item.personal_due - item.personal_paid)),
                "channel_summary": rent_channel_summary_text(item),
            }
            for item in rent_issues
        ],
        "utility_items": [
            {
                "id": item.id,
                "status": item.status,
                "due_date": item.due_date.isoformat() if item.due_date else "",
                "label": f"ком. услуги {item.bill.period_start:%d.%m.%Y} -> {item.bill.period_end:%d.%m.%Y}",
                "debt": money(max(0.0, item.total_amount - item.paid_amount)),
                "service": item.bill.service.name,
                "period_label": f"{item.bill.period_start:%d.%m.%Y} -> {item.bill.period_end:%d.%m.%Y}",
            }
            for item in utility_issues
        ],
        "all_debt_total": money(rent_debt + utility_debt),
    }


def resolve_message_request(
    session: Session,
    payload: dict[str, Any],
) -> tuple[Lease, str, RentCharge | None, UtilityBillLine | None, str]:
    lease = session.get(Lease, int(payload["lease_id"]))
    if not lease or not lease.active:
        raise HTTPException(404, "Активный договор не найден")

    template_key = (payload.get("template_key") or "").strip()
    if template_key not in {"message_rent_due", "message_rent_overdue", "message_utility_bill", "message_all_debts", "custom"}:
        raise HTTPException(400, "Неизвестный шаблон сообщения")

    charge = session.get(RentCharge, int(payload["charge_id"])) if payload.get("charge_id") else None
    line = session.get(UtilityBillLine, int(payload["utility_line_id"])) if payload.get("utility_line_id") else None
    if charge and charge.lease_id != lease.id:
        raise HTTPException(400, "Этот арендный долг относится к другому жильцу")
    if line and line.lease_id != lease.id:
        raise HTTPException(400, "Этот коммунальный счёт относится к другому жильцу")
    custom_text = (payload.get("custom_text") or "").strip()
    return lease, template_key, charge, line, custom_text


def send_tenant_message(
    session: Session,
    lease: Lease,
    template_key: str,
    charge: RentCharge | None = None,
    line: UtilityBillLine | None = None,
    custom_text: str = "",
    note: str = "manual",
    utility_lines_override: list[UtilityBillLine] | None = None,
    utility_due_date_override: date | None = None,
) -> dict[str, Any]:
    chat_id = lease_chat_id(session, lease)
    if not chat_id:
        raise HTTPException(400, f"{lease.tenant.full_name} ещё не привязан к боту. Пусть сначала напишет /start.")
    text = render_message_text(
        session,
        template_key,
        lease,
        charge,
        line,
        custom_text,
        utility_lines_override=utility_lines_override,
        utility_due_date_override=utility_due_date_override,
    )
    if not text.strip():
        raise HTTPException(400, "Текст сообщения пустой")
    send_telegram_text(session, chat_id, text)
    session.add(
        MessageLog(
            lease_id=lease.id,
            rent_charge_id=charge.id if charge else None,
            utility_line_id=line.id if line else None,
            channel="telegram",
            template_key=template_key,
            status="sent",
            recipient_chat_id=str(chat_id),
            text=text,
            note=note,
        )
    )
    session.flush()
    return {
        "ok": True,
        "tenant": lease.tenant.full_name,
        "lease_id": lease.id,
        "template_key": template_key,
        "text": text,
    }


def utility_issue_targets(session: Session, bill: UtilityBill) -> list[dict[str, Any]]:
    due = resident_due_date(bill.service)
    by_lease: dict[int, list[UtilityBillLine]] = {}
    for line in bill.lines:
        if not line.lease_id:
            continue
        debt = max(0.0, line.total_amount - line.paid_amount)
        if debt <= 0.009:
            continue
        by_lease.setdefault(line.lease_id, []).append(line)

    previews: list[dict[str, Any]] = []
    for lease_id, selected_lines in sorted(by_lease.items()):
        lease = session.get(Lease, lease_id)
        if not lease or lease_ignored(session, lease.id):
            continue
        existing_lines = [
            item
            for item in outstanding_utility_lines(session, lease)
            if item.id not in {selected.id for selected in selected_lines}
        ]
        combined_lines = sorted(
            [*existing_lines, *selected_lines],
            key=lambda item: (item.bill.period_end, item.bill.service.name, item.id),
        )
        text = render_message_text(
            session,
            "message_utility_bill",
            lease,
            line=selected_lines[0],
            utility_lines_override=combined_lines,
            utility_due_date_override=due,
        )
        previews.append(
            {
                "lease_id": lease.id,
                "tenant": lease.tenant.full_name,
                "apartment": lease.apartment.name,
                "object": lease.apartment.object.name,
                "linked": tenant_chat_linked(session, lease),
                "text": text,
                "line_ids": [line.id for line in selected_lines],
                "all_line_ids": [line.id for line in combined_lines],
                "total": money(sum(max(0.0, item.total_amount - item.paid_amount) for item in selected_lines)),
            }
        )
    return previews


def reminder_sent_today(
    session: Session,
    template_key: str,
    *,
    rent_charge_id: int | None = None,
    utility_line_id: int | None = None,
    today: date | None = None,
) -> bool:
    today = today or date.today()
    query = select(MessageLog.id).where(
        MessageLog.template_key == template_key,
        func.date(MessageLog.created_at) == today.isoformat(),
    )
    if rent_charge_id is not None:
        query = query.where(MessageLog.rent_charge_id == rent_charge_id)
    if utility_line_id is not None:
        query = query.where(MessageLog.utility_line_id == utility_line_id)
    return bool(session.scalar(query.limit(1)))


def run_due_reminders(session: Session, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    now = local_now()
    cutoff = notification_cutoff_date(session)
    if not notifications_enabled(session):
        return {
            "enabled": False,
            "cutoff_date": cutoff.isoformat(),
            "sent": 0,
            "skipped_disabled": 1,
            "skipped_legacy": 0,
            "skipped_duplicate": 0,
            "skipped_unlinked": 0,
            "failed": 0,
        }

    summary = {
        "enabled": True,
        "cutoff_date": cutoff.isoformat(),
        "sent": 0,
        "skipped_disabled": 0,
        "skipped_legacy": 0,
        "skipped_duplicate": 0,
        "skipped_unlinked": 0,
        "failed": 0,
    }

    charges = session.scalars(
        select(RentCharge)
        .join(Lease)
        .join(Apartment)
        .where(Lease.active.is_(True), Apartment.active.is_(True))
        .order_by(RentCharge.due_date, RentCharge.id)
    ).all()
    for charge in charges:
        update_rent_charge_status(charge, today)
        template_key = ""
        if lease_ignored(session, charge.lease_id):
            summary["skipped_disabled"] += 1
            continue
        if charge.due_date < cutoff:
            summary["skipped_legacy"] += 1
            continue
        if not lease_chat_id(session, charge.lease):
            summary["skipped_unlinked"] += 1
            continue
        if charge.status == "pending" and charge.due_date == today:
            template_key = "message_rent_due"
        elif charge.status in {"overdue", "partial"} and charge.due_date <= today:
            template_key = "message_rent_overdue"
        if not template_key:
            continue
        latest = latest_message_log(session, rent_charge_id=charge.id)
        cadence = get_lease_cadence(session, charge.lease_id, template_key) or reminder_cadence(session, template_key)
        if not cadence_allows_send(cadence, latest.created_at if latest else None, now):
            summary["skipped_duplicate"] += 1
            continue
        try:
            send_tenant_message(session, charge.lease, template_key, charge=charge, note="auto")
            summary["sent"] += 1
        except HTTPException:
            summary["failed"] += 1

    lines = session.scalars(
        select(UtilityBillLine)
        .join(Lease, UtilityBillLine.lease_id == Lease.id)
        .join(Apartment, UtilityBillLine.apartment_id == Apartment.id)
        .where(Lease.active.is_(True), Apartment.active.is_(True))
        .order_by(UtilityBillLine.id)
    ).all()
    for line in lines:
        update_utility_line_status(line, today)
        if lease_ignored(session, line.lease_id):
            summary["skipped_disabled"] += 1
            continue
        if not line.due_date:
            continue
        if line.due_date < cutoff:
            summary["skipped_legacy"] += 1
            continue
        if not line.lease or not lease_chat_id(session, line.lease):
            summary["skipped_unlinked"] += 1
            continue
        if not (line.status in {"issued", "overdue", "partial"} and line.due_date <= today):
            continue
        latest = latest_message_log(session, utility_line_id=line.id)
        cadence = get_lease_cadence(session, line.lease_id or 0, "message_utility_bill") or reminder_cadence(session, "message_utility_bill")
        if not cadence_allows_send(cadence, latest.created_at if latest else None, now):
            summary["skipped_duplicate"] += 1
            continue
        try:
            send_tenant_message(session, line.lease, "message_utility_bill", line=line, note="auto")
            summary["sent"] += 1
        except HTTPException:
            summary["failed"] += 1

    return summary


def format_date(value: date | None) -> str:
    if not value:
        return ""
    month = MONTH_NAMES[value.month - 1]
    today = date.today()
    if value.year != today.year:
        return f"{value.day} {month} {value.year}"
    if value.month != today.month:
        return f"{value.day} {month}"
    return f"{value.day} число"


def format_full_date(value: date | None) -> str:
    if not value:
        return ""
    return value.strftime("%d.%m.%Y")


def message_sender_label(message: dict[str, Any]) -> str:
    sender = message.get("from") or {}
    full_name = " ".join(part for part in [sender.get("first_name"), sender.get("last_name")] if part).strip()
    username = normalize_telegram_handle(sender.get("username") or "")
    if full_name and username:
        return f"{full_name} (@{username})"
    if full_name:
        return full_name
    if username:
        return f"@{username}"
    return "неизвестный отправитель"


def receipt_channel_label(value: str) -> str:
    return {
        "ip": "ИП",
        "personal": "По номеру",
        "expense_fund": "Мне на расходы",
        "utilities": "коммуналка",
        "unknown": "неизвестно",
    }.get(value, value or "неизвестно")


def receipt_status_label(value: str) -> str:
    return {
        "accepted": "принят",
        "suspicious": "нужна проверка",
        "duplicate": "дубликат",
        "rejected": "отклонён",
    }.get(value, value or "неизвестно")


def receipt_storage_dir() -> Path:
    path = ROOT_DIR / "data" / "telegram_receipts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def telegram_media_descriptor(message: dict[str, Any]) -> dict[str, str]:
    document = message.get("document")
    if document:
        return {
            "file_id": document.get("file_id") or "",
            "file_name": Path(document.get("file_name") or "telegram-document.bin").name,
            "mime_type": document.get("mime_type") or "",
            "file_unique_id": document.get("file_unique_id") or "",
        }
    photos = message.get("photo") or []
    if photos:
        largest = photos[-1]
        return {
            "file_id": largest.get("file_id") or "",
            "file_name": f"telegram-photo-{largest.get('file_unique_id') or utc_now().timestamp()}.jpg",
            "mime_type": "image/jpeg",
            "file_unique_id": largest.get("file_unique_id") or "",
        }
    return {"file_id": "", "file_name": "", "mime_type": "", "file_unique_id": ""}


def save_telegram_media(session: Session, message: dict[str, Any]) -> Path | None:
    token = telegram_token(session)
    media = telegram_media_descriptor(message)
    file_id = media["file_id"]
    name = media["file_name"]
    if not token or not file_id:
        return None
    info = telegram_file_info(token, file_id)
    file_path = info.get("file_path") or ""
    if not file_path:
        return None
    extension = Path(name).suffix or Path(file_path).suffix or ".bin"
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    target = receipt_storage_dir() / f"{stamp}-{Path(name).stem[:40]}{extension}"
    return download_telegram_file(token, file_path, target)


def receipt_candidate_lease(session: Session, message: dict[str, Any], parsed: dict[str, Any] | None, linked_lease: Lease | None) -> Lease | None:
    if linked_lease:
        return linked_lease
    if parsed and parsed.get("purpose"):
        return find_lease_by_receipt_purpose(session, parsed.get("purpose") or "")
    return None


def receipt_operation_fingerprint(parsed: dict[str, Any] | None) -> str:
    if not parsed:
        return ""
    parts = [
        str(parsed.get("source_bank") or "").strip().lower(),
        str(parsed.get("receipt_number") or "").strip().lower(),
        str(parsed.get("paid_at") or "").strip(),
        f"{float(parsed.get('amount') or 0):.2f}",
        str(parsed.get("recipient_name") or "").strip().lower(),
        str(parsed.get("recipient_account") or "").strip(),
        str(parsed.get("recipient_phone") or "").strip(),
        str(parsed.get("payer_name") or "").strip().lower(),
    ]
    if not any(parts[1:4]):
        return ""
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def receipt_document_sha256(saved_path: Path) -> str:
    return hashlib.sha256(saved_path.read_bytes()).hexdigest()


def build_receipt_details_payload(message: dict[str, Any], parsed: dict[str, Any] | None, saved_path: Path) -> dict[str, Any]:
    media = telegram_media_descriptor(message)
    payload = dict(parsed or {})
    payload["_meta"] = {
        "received_at": utc_now().isoformat(),
        "document_sha256": receipt_document_sha256(saved_path),
        "operation_fingerprint": receipt_operation_fingerprint(parsed),
        "telegram_chat_id": str((message.get("chat") or {}).get("id") or ""),
        "telegram_message_id": str(message.get("message_id") or ""),
        "telegram_file_id": media.get("file_id") or "",
        "telegram_file_unique_id": media.get("file_unique_id") or "",
        "file_name": saved_path.name,
    }
    return payload


def duplicate_receipt(session: Session, document_sha256: str, operation_fingerprint: str) -> PaymentReceipt | None:
    if not document_sha256 and not operation_fingerprint:
        return None
    receipts = session.scalars(select(PaymentReceipt).where(PaymentReceipt.source == "telegram").order_by(PaymentReceipt.id.desc())).all()
    for receipt in receipts:
        parsed = parse_receipt_details(receipt)
        meta = receipt_meta(parsed)
        if document_sha256 and meta.get("document_sha256") == document_sha256:
            return receipt
        if operation_fingerprint and meta.get("operation_fingerprint") == operation_fingerprint:
            return receipt
    return None


def recipient_mismatch_issue(channel: str, issues: list[str]) -> bool:
    if channel == "ip":
        return any("получатель ип" in issue.lower() or "счёт получателя ип" in issue.lower() for issue in issues)
    if channel == "personal":
        return any(
            marker in issue.lower()
            for marker in [
                "получатель перевода",
                "номер получателя перевода",
            ]
            for issue in issues
        )
    return False


def create_personal_priority_receipts(
    session: Session,
    lease: Lease,
    amount: float,
    *,
    paid_at: datetime,
    source: str,
    status: str,
    recipient_name: str = "",
    recipient_details: str = "",
    notes: str = "",
    file_path: str = "",
) -> list[PaymentReceipt]:
    receipts: list[PaymentReceipt] = []
    remaining = money(amount)
    cutoff = allocation_cutoff_date(session)
    utility_candidates_now = [line for line in utility_line_candidates(session, lease.id) if debt_visible_by_cutoff(line.due_date, cutoff)]
    generate_rent_charges(session, until=(paid_at.date() if isinstance(paid_at, datetime) else paid_at) + timedelta(days=40))
    personal_candidates_now: list[RentCharge] = []
    current_charge: RentCharge | None = None
    paid_day = paid_at.date() if isinstance(paid_at, datetime) else paid_at
    all_charges = session.scalars(
        select(RentCharge)
        .where(RentCharge.lease_id == lease.id, RentCharge.due_date >= cutoff)
        .order_by(RentCharge.due_date.desc(), RentCharge.id.desc())
    ).all()
    for charge in all_charges:
        personal_debt = money(max(0.0, charge.personal_due - charge.personal_paid))
        if personal_debt <= EPS:
            continue
        if charge.due_date.year == paid_day.year and charge.due_date.month == paid_day.month:
            current_charge = charge
        elif charge.due_date <= paid_day:
            personal_candidates_now.append(charge)
    if current_charge:
        personal_candidates_now.insert(0, current_charge)

    if utility_candidates_now:
        for line in utility_candidates_now:
            debt = money(max(0.0, line.total_amount - line.paid_amount))
            portion = min(remaining, debt)
            if portion <= EPS:
                continue
            receipts.append(
                PaymentReceipt(
                    lease_id=lease.id,
                    utility_line_id=line.id,
                    apartment_id=lease.apartment_id,
                    amount=portion,
                    channel="utilities",
                    paid_at=paid_at,
                    source=source,
                    status=status,
                    recipient_name=recipient_name,
                    recipient_details=recipient_details,
                    file_path=file_path,
                    notes=notes,
                )
            )
            remaining = money(remaining - portion)
            if remaining <= EPS:
                break
        if receipts and remaining > EPS and not personal_candidates_now:
            receipts[-1].amount = money(receipts[-1].amount + remaining)
            remaining = 0.0

    if remaining > EPS and personal_candidates_now:
        for charge in personal_candidates_now:
            personal_debt = money(max(0.0, charge.personal_due - charge.personal_paid))
            portion = min(remaining, personal_debt)
            if portion <= EPS:
                continue
            receipts.append(
                PaymentReceipt(
                    lease_id=lease.id,
                    rent_charge_id=charge.id,
                    apartment_id=lease.apartment_id,
                    amount=portion,
                    channel="personal",
                    paid_at=paid_at,
                    source=source,
                    status=status,
                    recipient_name=recipient_name,
                    recipient_details=recipient_details,
                    file_path=file_path,
                    notes=notes,
                )
            )
            remaining = money(remaining - portion)
            if remaining <= EPS:
                break
        if receipts and remaining > EPS:
            receipts[-1].amount = money(receipts[-1].amount + remaining)
            remaining = 0.0

    if not receipts:
        raise ValueError("по переводу не найдено долга для зачёта")

    session.add_all(receipts)
    session.flush()
    recalculate_lease_balances(session, lease.id)
    return receipts


def apply_receipt_match(session: Session, lease: Lease, parsed: dict[str, Any]) -> tuple[str, str, int | None, list[str], str]:
    amount = float(parsed.get("amount") or 0)
    channel = detect_receipt_channel(parsed)
    issues = receipt_validation_issues(parsed, get_settings(session), channel)
    allocation_comment = ""
    paid_at = datetime.fromisoformat(parsed["paid_at"]) if parsed.get("paid_at") else None
    cutoff = allocation_cutoff_date(session)
    if amount <= 0:
        issues.append("в чеке не удалось определить положительную сумму")

    if channel == "ip":
        plan, remaining, decision = build_rent_plan(
            session,
            lease,
            "ip",
            amount,
            exact_only=False,
            paid_at=paid_at,
            prefer_document_month=True,
            cutoff=cutoff,
        )
        allocation_comment = describe_rent_allocation_decision(decision)
        if not plan or remaining > EPS:
            issues.append("не удалось честно разложить платёж по аренде")
        if issues:
            return "suspicious", "review", None, issues, allocation_comment
        return "accepted", "rent", plan[0][0].id, [], allocation_comment

    if channel == "personal":
        if recipient_mismatch_issue(channel, issues):
            return "rejected", "review", None, issues, ""
        if issues:
            return "suspicious", "review", None, issues, ""
        utility_candidates_now = [line for line in utility_line_candidates(session, lease.id) if debt_visible_by_cutoff(line.due_date, cutoff)]
        if utility_candidates_now:
            return "accepted", "utility", utility_candidates_now[0].id, [], "зачёт перевода по приоритету ушёл в коммуналку"
        paid_day = paid_at.date() if isinstance(paid_at, datetime) else paid_at.date() if paid_at else date.today()
        generate_rent_charges(session, until=paid_day + timedelta(days=40))
        personal_candidates_now = []
        for charge in session.scalars(
            select(RentCharge)
            .where(RentCharge.lease_id == lease.id, RentCharge.due_date >= cutoff)
            .order_by(RentCharge.due_date.desc(), RentCharge.id.desc())
        ).all():
            personal_debt = money(max(0.0, charge.personal_due - charge.personal_paid))
            if personal_debt <= EPS:
                continue
            if charge.due_date.year == paid_day.year and charge.due_date.month == paid_day.month:
                personal_candidates_now = [charge, *[item for item in personal_candidates_now if item.id != charge.id]]
            elif charge.due_date <= paid_day:
                personal_candidates_now.append(charge)
        if personal_candidates_now:
            return "accepted", "rent", personal_candidates_now[0].id, [], "зачёт перевода по приоритету ушёл в перевод по номеру телефона"
        return "suspicious", "review", None, ["по переводу не найдено долга для зачёта"], ""

    if recipient_mismatch_issue(channel, issues):
        return "rejected", "review", None, issues, ""
    return "suspicious", "review", None, ["тип перевода пока не удалось определить"], ""


def owner_receipt_alert_text(
    session: Session,
    lease: Lease | None,
    parsed: dict[str, Any] | None,
    receipt_status: str,
    issues: list[str],
    sender_label: str,
    allocation_comment: str = "",
) -> str:
    summary_bits = [sender_label]
    if parsed:
        if parsed.get("recipient_name"):
            summary_bits.append(f"получатель: {parsed['recipient_name']}")
        if parsed.get("purpose"):
            summary_bits.append(f"назначение: {parsed['purpose']}")
    if allocation_comment:
        summary_bits.append(f"зачёт: {allocation_comment}")
    if issues:
        summary_bits.append("вопросы: " + "; ".join(issues))
    context = {
        "tenant_name": lease.tenant.full_name if lease else sender_label,
        "apartment": lease.apartment.name if lease else "не определена",
        "amount": money_text(float(parsed.get("amount") or 0)) if parsed else "0,00 ₽",
        "channel": receipt_channel_label(detect_receipt_channel(parsed or {})),
        "receipt_status": receipt_status_label(receipt_status),
        "receipt_summary": ". ".join(summary_bits),
    }
    return fill_template(message_template(session, "message_owner_receipt_alert"), context)


def handle_tenant_receipt_message(session: Session, message: dict[str, Any], linked_lease: Lease | None) -> None:
    chat_id = (message.get("chat") or {}).get("id")
    if not chat_id:
        return
    owner_id = telegram_owner_chat_id(session)
    saved_path = save_telegram_media(session, message)
    if not saved_path:
        if owner_id and message.get("message_id"):
            try:
                copy_message(
                    telegram_token(session),
                    to_chat_id=owner_id,
                    from_chat_id=chat_id,
                    message_id=int(message["message_id"]),
                    reply_markup=app_keyboard(app_base_url(session)),
                )
            except RuntimeError:
                pass
        send_telegram_text(session, chat_id, message_template(session, "message_receipt_review"), tenant_keyboard())
        return

    parsed: dict[str, Any] | None = None
    if saved_path.suffix.lower() == ".pdf":
        try:
            parsed = parse_receipt_file(saved_path)
        except Exception:
            parsed = None

    receipt_payload = build_receipt_details_payload(message, parsed, saved_path)
    meta = receipt_meta(receipt_payload)
    receipt_details = json.dumps(receipt_payload, ensure_ascii=False)
    lease = receipt_candidate_lease(session, message, parsed, linked_lease)
    status = "suspicious"
    match_type = "review"
    linked_id: int | None = None
    allocation_comment = ""
    issues = ["чек сохранён, но не удалось ничего распознать"]
    duplicate = duplicate_receipt(
        session,
        str(meta.get("document_sha256") or ""),
        str(meta.get("operation_fingerprint") or ""),
    )
    if duplicate:
        status = "duplicate"
        issues = [f"этот чек уже есть в системе с {duplicate.created_at:%d.%m.%Y}, повторно не зачитываю"]
    elif parsed and lease:
        status, match_type, linked_id, issues, allocation_comment = apply_receipt_match(session, lease, parsed)
    elif parsed and not lease:
        issues = ["чек разобран, но арендатор по нему пока не определён"]

    channel = detect_receipt_channel(parsed or {})
    paid_at = datetime.fromisoformat(parsed["paid_at"]) if parsed and parsed.get("paid_at") else utc_now()
    stored_path = str(saved_path.relative_to(ROOT_DIR))
    if status == "accepted" and lease and parsed:
        try:
            if channel == "personal":
                create_personal_priority_receipts(
                    session,
                    lease,
                    float(parsed.get("amount") or 0),
                    paid_at=paid_at,
                    source="telegram",
                    status="accepted",
                    recipient_name=parsed.get("recipient_name") or "",
                    recipient_details=receipt_details,
                    notes="автоматически принято из Telegram",
                    file_path=stored_path,
                )
            elif match_type == "rent":
                create_rent_receipts(
                    session,
                    lease,
                    "ip" if channel == "ip" else "personal",
                    float(parsed.get("amount") or 0),
                    paid_at=paid_at,
                    source="telegram",
                    status="accepted",
                    recipient_name=parsed.get("recipient_name") or "",
                    recipient_details=receipt_details,
                    notes="автоматически принято из Telegram",
                    file_path=stored_path,
                    exact_only=channel == "personal",
                    prefer_document_month=True,
                    cutoff=allocation_cutoff_date(session),
                )
            elif match_type == "utility":
                create_utility_receipts(
                    session,
                    lease,
                    float(parsed.get("amount") or 0),
                    paid_at=paid_at,
                    source="telegram",
                    status="accepted",
                    recipient_name=parsed.get("recipient_name") or "",
                    recipient_details=receipt_details,
                    notes="автоматически принято из Telegram",
                    file_path=stored_path,
                    exact_only=True,
                )
            else:
                status = "suspicious"
                issues = ["бот не понял, куда зачесть чек"]
        except ValueError as exc:
            status = "suspicious"
            match_type = "review"
            linked_id = None
            issues = [str(exc)]

    if status != "accepted":
        session.add(
            PaymentReceipt(
                lease_id=lease.id if lease else None,
                apartment_id=lease.apartment_id if lease else None,
                amount=float((parsed or {}).get("amount") or 0),
                channel=channel or "unknown",
                paid_at=paid_at,
                source="telegram",
                status="duplicate" if status == "duplicate" else "rejected" if status == "rejected" else "suspicious",
                recipient_name=(parsed or {}).get("recipient_name") or "",
                recipient_details=receipt_details,
                file_path=stored_path,
                notes="; ".join(issues),
            )
        )

    if status == "accepted":
        send_telegram_text(session, chat_id, message_template(session, "message_receipt_received"), tenant_keyboard())
    elif status == "duplicate":
        send_telegram_text(session, chat_id, message_template(session, "message_receipt_duplicate"), tenant_keyboard())
    elif status == "rejected":
        send_telegram_text(session, chat_id, "Некорректный получатель, отправляю владельцу для рассмотрения.", tenant_keyboard())
    else:
        send_telegram_text(session, chat_id, message_template(session, "message_receipt_review"), tenant_keyboard())

    if owner_id:
        send_telegram_text(
            session,
            owner_id,
            owner_receipt_alert_text(session, lease, parsed, status, issues, message_sender_label(message), allocation_comment),
            app_keyboard(app_base_url(session)),
        )
        if message.get("message_id"):
            try:
                copy_message(
                    telegram_token(session),
                    to_chat_id=owner_id,
                    from_chat_id=chat_id,
                    message_id=int(message["message_id"]),
                    reply_markup=app_keyboard(app_base_url(session)),
                )
            except RuntimeError:
                pass


def send_telegram_text(session: Session, chat_id: int | str, text: str, keyboard: dict[str, Any] | None = None) -> dict[str, Any]:
    token = telegram_token(session)
    if not token:
        raise HTTPException(400, "Не задан токен Telegram-бота")
    try:
        return send_message(token, chat_id, text, reply_markup=keyboard)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


def tenant_requisites_text(session: Session) -> str:
    settings = get_settings(session)
    lines = [
        "Реквизиты для перевода на ИП:",
        f"Наименование: {settings.get('ip_recipient_name') or 'не задано'}",
        f"ИНН: {settings.get('ip_recipient_inn') or 'не задано'}",
        f"ОГРНИП: {settings.get('ip_recipient_ogrnip') or 'не задано'}",
        f"Расчётный счёт: {settings.get('ip_recipient_account') or 'не задано'}",
        f"Банк: {settings.get('ip_recipient_bank') or 'не задано'}",
        f"БИК банка: {settings.get('ip_recipient_bik') or 'не задано'}",
        f"Корр. счёт банка: {settings.get('ip_recipient_correspondent_account') or 'не задано'}",
        f"ИНН банка: {settings.get('ip_recipient_bank_inn') or 'не задано'}",
        f"КПП банка: {settings.get('ip_recipient_bank_kpp') or 'не задано'}",
        "",
        "Реквизиты для перевода по номеру телефона (доп. платёж):",
        "89133854441 Сбербанк Эрнест К.",
    ]
    return "\n".join(lines)


def tenant_help_text() -> str:
    return "\n".join(
        [
            "Что умеет бот для жильца:",
            "• принять чек PDF документом и попытаться зачесть его автоматически;",
            "• если что-то не понял — передать чек владельцу на ручную проверку;",
            "• показать реквизиты по кнопке «Реквизиты» или по команде /requisites;",
            "• показать все текущие долги по кнопке «Все долги» или по команде /debts;",
            "• подтвердить, что чек уже был получен, чтобы не крутить один и тот же документ по кругу.",
        ]
    )


def telegram_help_text() -> str:
    return "\n".join(
        [
            "Команды бота:",
            "/start - приветствие",
            "/id - показать chat id",
            "/status - статус пульта",
            "/reports - открытые месячные отчёты",
            "/requisites - показать реквизиты для оплаты",
            "/run_reminders - прогнать напоминания",
            "/app - открыть пульт",
            "/help - подсказка",
        ]
    )


def handle_telegram_message(session: Session, message: dict[str, Any]) -> None:
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if not chat_id:
        return

    linked_lease = maybe_link_tenant_chat(session, message)
    text = (message.get("text") or "").strip()
    command, _args = parse_command(text)
    base_url = app_base_url(session)
    reports = build_monthly_reports(session)
    owner_id = telegram_owner_chat_id(session)
    is_owner = owner_chat_allowed(session, chat_id)

    if message.get("contact") and not is_owner:
        if linked_lease:
            send_telegram_text(
                session,
                chat_id,
                f"Готово, привязал этот чат к квартире {linked_lease.apartment.name}.",
                tenant_keyboard(),
            )
        else:
            send_telegram_text(session, chat_id, "Не нашёл активного жильца с таким телефоном в базе.")
        return

    if command in {"/start", "/id"}:
        if command == "/id":
            send_telegram_text(session, chat_id, f"Ваш chat id: {chat_id}")
            return
        if is_owner:
            send_telegram_text(
                session,
                chat_id,
                "Бот подключён. Можно смотреть статус, отчёты и открывать пульт с телефона.",
                app_keyboard(base_url),
            )
            return
        if linked_lease:
            send_telegram_text(
                session,
                chat_id,
                f"Привет. Я привязал этот чат к квартире {linked_lease.apartment.name}. Теперь сюда можно присылать чеки PDF документом, спрашивать реквизиты и получать ответы по платежам.",
                tenant_keyboard(),
            )
            return
        send_telegram_text(
            session,
            chat_id,
            "Если вы жилец без @username, нажмите «Привязать по телефону». Пульт владельца через бота не выдаётся.",
            tenant_keyboard(),
        )
        return

    if command == "/help":
        if is_owner:
            send_telegram_text(session, chat_id, telegram_help_text(), app_keyboard(base_url))
        else:
            send_telegram_text(session, chat_id, tenant_help_text(), tenant_keyboard())
        return

    if command == "/requisites" or text.lower() == "реквизиты":
        # Показываем реквизиты только владельцу или привязанным жильцам
        if is_owner or linked_lease:
            send_telegram_text(
                session,
                chat_id,
                tenant_requisites_text(session),
                tenant_keyboard() if not is_owner else app_keyboard(base_url),
            )
        else:
            send_telegram_text(session, chat_id, "Реквизиты доступны только для привязанных жильцов.")
        return

    if command == "/debts" or text.lower() == "все долги":
        if linked_lease:
            send_telegram_text(
                session,
                chat_id,
                render_message_text(session, "message_all_debts", linked_lease),
                tenant_keyboard(),
            )
        elif is_owner:
            send_telegram_text(session, chat_id, "Команда /debts работает для привязанного чата жильца. У тебя для этого есть пульт, а зачем ещё кружить.")
        else:
            send_telegram_text(session, chat_id, "Сначала привяжи чат через @username в базе и /start.")
        return

    # Обработка файлов от жильцов - до проверки owner_id
    if not is_owner and (message.get("document") or message.get("photo")):
        if linked_lease:
            handle_tenant_receipt_message(session, message, linked_lease)
        return

    if not owner_id:
        send_telegram_text(
            session,
            chat_id,
            f"Owner chat id ещё не настроен. Отправь /id и сохрани это значение в приложении. Сейчас chat id: {chat_id}",
        )
        return

    if not is_owner:
        send_telegram_text(
            session,
            chat_id,
            tenant_help_text(),
            tenant_keyboard(),
        )
        return

    if command == "/status":
        send_telegram_text(session, chat_id, build_status_message(build_dashboard(session)), app_keyboard(base_url))
        return
    if command == "/reports":
        send_telegram_text(session, chat_id, build_reports_message(reports), app_keyboard(base_url, reports))
        return
    if command == "/run_reminders":
        summary = run_due_reminders(session)
        session.commit()
        send_telegram_text(
            session,
            chat_id,
            "\n".join(
                [
                    "Напоминания прогнаны.",
                    f"Отправлено: {summary['sent']}",
                    f"Дубликаты за сегодня: {summary['skipped_duplicate']}",
                    f"Старые долги под молчанием: {summary['skipped_legacy']}",
                    f"Без привязки к боту: {summary['skipped_unlinked']}",
                    f"Ошибки: {summary['failed']}",
                ]
            ),
            app_keyboard(base_url),
        )
        return
    if command == "/app":
        if is_owner:
            send_telegram_text(session, chat_id, "Открываю пульт.", app_keyboard(base_url))
        else:
            send_telegram_text(session, chat_id, "Доступ к пульту только у владельца.")
        return
    if command == "/requisites":
        send_telegram_text(session, chat_id, tenant_requisites_text(session), app_keyboard(base_url))
        return
    if message.get("document") or message.get("photo"):
        send_telegram_text(
            session,
            chat_id,
            "Файл получил. OCR и разбор чеков ещё не включены, но вход для этого уже подготовлен.",
        )
        return

    send_telegram_text(session, chat_id, telegram_help_text(), app_keyboard(base_url))


@app.post("/api/integrations/telegram/webhook")
async def telegram_webhook(request: Request, payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, bool]:
    secret = telegram_secret(session)
    if secret:
        provided = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if provided != secret:
            raise HTTPException(403, "Неверный Telegram secret token")
    if payload.get("message"):
        handle_telegram_message(session, payload["message"])
    session.commit()
    return {"ok": True}


@app.post("/api/integrations/telegram/set-webhook")
def telegram_set_webhook(session: Session = Depends(get_session)) -> dict[str, Any]:
    token = telegram_token(session)
    base_url = app_base_url(session)
    if not token:
        raise HTTPException(400, "Сначала сохрани Telegram bot token")
    if not base_url.startswith("https://"):
        raise HTTPException(400, "Для webhook нужен публичный HTTPS URL в app_base_url")
    payload: dict[str, Any] = {
        "url": f"{base_url}/api/integrations/telegram/webhook",
        "allowed_updates": ["message"],
    }
    secret = telegram_secret(session)
    if secret:
        payload["secret_token"] = secret
    try:
        webhook_result = telegram_api_request(token, "setWebhook", payload)
        commands_result = telegram_api_request(token, "setMyCommands", {"commands": owner_commands()})
        return {
            "ok": bool(webhook_result.get("ok")) and bool(commands_result.get("ok")),
            "description": webhook_result.get("description", ""),
            "commands_configured": bool(commands_result.get("ok")),
        }
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/integrations/telegram/webhook-info")
def telegram_webhook_info(session: Session = Depends(get_session)) -> dict[str, Any]:
    token = telegram_token(session)
    if not token:
        raise HTTPException(400, "Сначала сохрани Telegram bot token")
    try:
        return telegram_api_request(token, "getWebhookInfo", {})
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/integrations/telegram/send-test")
def telegram_send_test(session: Session = Depends(get_session)) -> dict[str, Any]:
    owner_id = telegram_owner_chat_id(session)
    if not owner_id:
        raise HTTPException(400, "Сначала сохрани Telegram owner chat id")
    dashboard = build_dashboard(session)
    send_telegram_text(
        session,
        owner_id,
        build_status_message(dashboard),
        app_keyboard(app_base_url(session), dashboard.get("monthly_reports")),
    )
    return {"ok": True}


@app.get("/api/messages/targets")
def message_targets(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    leases = session.scalars(
        select(Lease).join(Apartment).where(Lease.active.is_(True), Apartment.active.is_(True)).order_by(Lease.start_date.desc())
    ).all()
    return [serialize_message_target(session, lease) for lease in leases if not lease_ignored(session, lease.id)]


@app.post("/api/messages/preview")
def preview_message_to_tenant(payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    lease, template_key, charge, line, custom_text = resolve_message_request(session, payload)
    text = render_message_text(session, template_key, lease, charge, line, custom_text)
    if not text.strip():
        raise HTTPException(400, "Текст сообщения пустой")
    return {
        "lease_id": lease.id,
        "tenant": lease.tenant.full_name,
        "apartment": lease.apartment.name,
        "object": lease.apartment.object.name,
        "template_key": template_key,
        "template_label": reminder_label(template_key),
        "linked": tenant_chat_linked(session, lease),
        "text": text,
        "payload": {
            "lease_id": lease.id,
            "template_key": template_key,
            "charge_id": charge.id if charge else None,
            "utility_line_id": line.id if line else None,
            "custom_text": custom_text,
        },
    }


@app.post("/api/messages/send")
def send_message_to_tenant(payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    lease, template_key, charge, line, custom_text = resolve_message_request(session, payload)
    result = send_tenant_message(
        session,
        lease,
        template_key,
        charge=charge,
        line=line,
        custom_text=custom_text,
    )
    session.commit()
    return result


@app.post("/api/reminders/run")
def run_reminders_now(session: Session = Depends(get_session)) -> dict[str, Any]:
    summary = run_due_reminders(session)
    session.commit()
    return summary


@app.get("/api/objects")
def list_objects(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    return [serialize_object(obj) for obj in session.scalars(select(RentalObject).order_by(RentalObject.name)).all()]


@app.post("/api/objects")
def create_object(payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    obj = RentalObject(
        name=(payload.get("name") or "").strip(),
        short_code=(payload.get("short_code") or "").strip(),
        notes=payload.get("notes") or "",
    )
    if not obj.name:
        raise HTTPException(400, "Название объекта обязательно")
    session.add(obj)
    session.commit()
    session.refresh(obj)
    return serialize_object(obj)


@app.post("/api/apartments")
def create_apartment(payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    apartment = Apartment(
        object_id=int(payload["object_id"]),
        name=(payload.get("name") or "").strip(),
        sort_order=int(payload.get("sort_order") or 0),
        odn_share_percent=float(payload.get("odn_share_percent") or 0),
        active=payload.get("active", True) not in {False, "false", "0", 0},
    )
    if not apartment.name:
        raise HTTPException(400, "Название квартиры обязательно")
    session.add(apartment)
    session.commit()
    session.refresh(apartment)
    return serialize_apartment(apartment)


@app.patch("/api/apartments/{apartment_id}")
def update_apartment(apartment_id: int, payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    apartment = session.get(Apartment, apartment_id)
    if not apartment:
        raise HTTPException(404, "Квартира не найдена")
    if "name" in payload:
        apartment.name = (payload.get("name") or apartment.name).strip()
    if "sort_order" in payload:
        apartment.sort_order = int(payload.get("sort_order") or 0)
    if "odn_share_percent" in payload:
        apartment.odn_share_percent = float(payload.get("odn_share_percent") or 0)
    if "active" in payload:
        apartment.active = payload.get("active") not in {False, "false", "0", 0}
    session.flush()
    generate_rent_charges(session)
    session.commit()
    session.refresh(apartment)
    return serialize_apartment(apartment)


@app.get("/api/tenants")
def list_tenants(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    return [serialize_tenant(tenant) for tenant in session.scalars(select(Tenant).order_by(Tenant.active.desc(), Tenant.full_name)).all()]


@app.get("/api/leases")
def list_leases(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    return [serialize_lease(lease, session) for lease in session.scalars(select(Lease).order_by(Lease.active.desc(), Lease.start_date.desc())).all()]


@app.post("/api/leases/onboard")
def onboard_tenant(payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    apartment_id = int(payload.get("apartment_id") or 0)
    if not apartment_id:
        raise HTTPException(400, "Выберите свободную квартиру")
    apartment = session.get(Apartment, apartment_id)
    if not apartment:
        raise HTTPException(404, "Квартира не найдена")
    active_lease = next(
        (
            lease
            for lease in session.scalars(select(Lease).where(Lease.apartment_id == apartment.id, Lease.active.is_(True))).all()
            if not lease_ignored(session, lease.id)
        ),
        None,
    )
    if active_lease:
        raise HTTPException(400, "В квартире уже есть активный жилец. Сначала оформите выезд.")

    start = parse_date(payload.get("start_date"), date.today())
    payment_day = int(payload.get("payment_day") or start.day)
    tenant = Tenant(
        full_name=(payload.get("full_name") or "Без имени").strip(),
        phone=payload.get("phone") or "",
        telegram=payload.get("telegram") or "",
        whatsapp=payload.get("whatsapp") or "",
        notes=payload.get("tenant_notes") or "",
    )
    session.add(tenant)
    session.flush()
    lease = Lease(
        apartment_id=apartment.id,
        tenant_id=tenant.id,
        start_date=start,
        payment_day=payment_day,
        ip_amount=float(payload.get("ip_amount") or 0),
        personal_amount=float(payload.get("personal_amount") or 0),
        deposit_amount=float(payload.get("deposit_amount") or 0),
        deposit_location=payload.get("deposit_location") or "",
        deposit_terms=payload.get("deposit_terms") or "",
        notes=payload.get("notes") or "",
    )
    session.add(lease)
    session.flush()
    if "ignored" in payload:
        set_lease_ignored(session, lease.id, setting_bool_value(payload.get("ignored")))
    generate_rent_charges(session)
    session.commit()
    session.refresh(lease)
    return serialize_lease(lease, session)


@app.patch("/api/leases/{lease_id}")
def update_lease(lease_id: int, payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    lease = session.get(Lease, lease_id)
    if not lease:
        raise HTTPException(404, "Договор не найден")

    was_ignored = lease_ignored(session, lease.id)
    apartment_id = int(payload.get("apartment_id") or lease.apartment_id)
    apartment = session.get(Apartment, apartment_id)
    if not apartment:
        raise HTTPException(404, "Квартира не найдена")
    if apartment.id != lease.apartment_id:
        active_lease = session.scalar(select(Lease).where(Lease.apartment_id == apartment.id, Lease.active.is_(True), Lease.id != lease.id))
        if active_lease:
            raise HTTPException(400, "В квартире уже есть активный жилец. Сначала оформите выезд.")
        lease.apartment_id = apartment.id

    start = parse_date(payload.get("start_date"), lease.start_date)
    payment_day = int(payload.get("payment_day") or start.day)

    lease.start_date = start
    lease.payment_day = payment_day
    lease.ip_amount = float(payload.get("ip_amount") or 0)
    lease.personal_amount = float(payload.get("personal_amount") or 0)
    lease.deposit_amount = float(payload.get("deposit_amount") or 0)
    lease.deposit_location = payload.get("deposit_location") or ""
    lease.deposit_terms = payload.get("deposit_terms") or ""
    lease.notes = payload.get("notes") or ""
    if was_ignored and IGNORE_LEASE_MARK not in lease.notes:
        lease.notes = (lease.notes + "\n" + IGNORE_LEASE_MARK).strip()
    if "end_date" in payload:
        raw_end = str(payload.get("end_date") or "").strip()
        lease.end_date = parse_date(raw_end) if raw_end else None
        lease.active = not (lease.end_date and lease.end_date <= date.today())
        lease.tenant.active = lease.active
    if "ignored" in payload:
        set_lease_ignored(session, lease.id, setting_bool_value(payload.get("ignored")))

    lease.tenant.full_name = (payload.get("full_name") or "Без имени").strip()
    lease.tenant.phone = payload.get("phone") or ""
    lease.tenant.telegram = payload.get("telegram") or ""
    lease.tenant.whatsapp = payload.get("whatsapp") or ""
    tenant_notes = payload.get("tenant_notes")
    if tenant_notes is not None:
        lease.tenant.notes = tenant_notes

    session.flush()
    generate_rent_charges(session)
    session.commit()
    session.refresh(lease)
    return serialize_lease(lease, session)


@app.delete("/api/leases/{lease_id}")
def delete_lease(lease_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    lease = session.get(Lease, lease_id)
    if not lease:
        raise HTTPException(404, "Договор не найден")
    tenant = lease.tenant
    session.execute(delete(MessageLog).where(MessageLog.lease_id == lease.id))
    for receipt in session.scalars(select(PaymentReceipt).where(PaymentReceipt.lease_id == lease.id)).all():
        linked_expense = linked_expense_fund_record(session, receipt.id)
        if linked_expense:
            session.delete(linked_expense)
        session.delete(receipt)
    for line in session.scalars(select(UtilityBillLine).where(UtilityBillLine.lease_id == lease.id)).all():
        line.lease_id = None
        line.status = "not_required"
    session.execute(delete(RentCharge).where(RentCharge.lease_id == lease.id))
    set_lease_ignored(session, lease.id, False)
    session.delete(lease)
    session.flush()
    if tenant and not session.scalar(select(Lease.id).where(Lease.tenant_id == tenant.id).limit(1)):
        session.delete(tenant)
    session.commit()
    return {"ok": True}


@app.post("/api/leases/{lease_id}/move-out")
def move_out(lease_id: int, payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    lease = session.get(Lease, lease_id)
    if not lease:
        raise HTTPException(404, "Договор не найден")
    end = parse_date(payload.get("end_date"), date.today())
    lease.end_date = end
    lease.active = False
    lease.tenant.active = False
    if payload.get("notes"):
        lease.notes = (lease.notes + "\n" + payload["notes"]).strip()

    for charge in lease.rent_charges:
        update_rent_charge_status(charge)

    rent_debt = sum(
        max(0, charge.ip_due - charge.ip_paid) + max(0, charge.personal_due - charge.personal_paid)
        for charge in lease.rent_charges
        if charge.period_start <= end and charge.status not in {"paid", "paid_ahead"}
    )
    utility_lines = session.scalars(select(UtilityBillLine).where(UtilityBillLine.lease_id == lease.id)).all()
    utility_debt = sum(max(0, line.total_amount - line.paid_amount) for line in utility_lines if line.status != "paid")
    paid_charges = [charge for charge in lease.rent_charges if charge.status in {"paid", "paid_ahead"}]
    last_paid_day = max((charge.period_end for charge in paid_charges), default=lease.start_date - timedelta(days=1))

    session.commit()
    return {
        "lease": serialize_lease(lease, session),
        "summary": {
            "full_months_lived": full_months_lived(lease, end),
            "last_paid_day": last_paid_day.isoformat(),
            "rent_debt": money(rent_debt),
            "utility_debt": money(utility_debt),
            "deposit_amount": lease.deposit_amount,
            "deposit_location": lease.deposit_location,
            "deposit_terms": lease.deposit_terms,
            "final_total_due": money(rent_debt + utility_debt),
        },
    }


@app.get("/api/rent-charges")
def list_rent_charges(
    start: str | None = None,
    end: str | None = None,
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    generate_rent_charges(session)
    start_date, end_date = current_month_range()
    if start:
        start_date = parse_date(start)
    if end:
        end_date = parse_date(end)
    charges = session.scalars(
        select(RentCharge)
        .join(Lease)
        .join(Apartment)
        .where(Lease.active.is_(True), Apartment.active.is_(True))
        .where(RentCharge.due_date >= start_date, RentCharge.due_date <= end_date)
        .order_by(RentCharge.due_date, RentCharge.id)
    ).all()
    charges = [charge for charge in charges if not lease_ignored(session, charge.lease_id)]
    session.commit()
    return [serialize_rent_charge(charge, session) for charge in charges]


@app.post("/api/rent-charges/generate")
def generate_charges(payload: dict[str, Any] | None = None, session: Session = Depends(get_session)) -> dict[str, Any]:
    until = parse_date((payload or {}).get("until"), date.today() + timedelta(days=90))
    created = generate_rent_charges(session, until=until)
    session.commit()
    return {"created": created}


@app.post("/api/rent-charges/{charge_id}/payments")
def add_rent_payment(charge_id: int, payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    charge = session.get(RentCharge, charge_id)
    if not charge:
        raise HTTPException(404, "начисление не найдено")
    channel = payload.get("channel")
    if channel not in {"ip", "personal", "expense_fund"}:
        raise HTTPException(400, "канал платежа должен быть ip, personal или expense_fund")
    amount = float(payload.get("amount") or 0)
    if amount <= 0:
        raise HTTPException(400, "сумма платежа должна быть больше нуля")

    receipts = create_rent_receipts(
        session,
        charge.lease,
        channel,
        amount,
        paid_at=datetime.fromisoformat(payload["paid_at"]) if payload.get("paid_at") else utc_now(),
        source=payload.get("source") or "manual",
        status=payload.get("status") or "accepted",
        recipient_name=payload.get("recipient_name") or "",
        recipient_details=payload.get("recipient_details") or "",
        notes=payload.get("notes") or "",
        exact_only=False,
        cutoff=allocation_cutoff_date(session),
    )
    sync_expense_fund_receipts(session, receipts)
    session.commit()
    session.refresh(charge)
    return serialize_rent_charge(charge, session)


@app.get("/api/leases/{lease_id}/payment-history")
def lease_payment_history(lease_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    lease = session.get(Lease, lease_id)
    if not lease:
        raise HTTPException(404, "аренда не найдена")
    receipts = session.scalars(
        select(PaymentReceipt)
        .where(PaymentReceipt.lease_id == lease_id)
        .order_by(PaymentReceipt.paid_at.desc(), PaymentReceipt.id.desc())
    ).all()
    return {
        "lease_id": lease.id,
        "tenant": lease.tenant.full_name,
        "apartment": lease.apartment.name,
        "current_year": date.today().year,
        "targets": lease_payment_target_options(session, lease),
        "receipts": [serialize_payment_receipt(receipt, session) for receipt in receipts],
    }


def ensure_rent_charge_for_month(session: Session, lease: Lease, year: int, month: int) -> RentCharge:
    if month < 1 or month > 12:
        raise HTTPException(400, "Месяц зачёта должен быть от 1 до 12")
    start, end = month_range(year, month)
    if start < RENT_GENERATION_START:
        raise HTTPException(400, "Начисления раньше января 2025 не создаются")
    generate_rent_charges(session, until=end + timedelta(days=40))
    session.flush()
    charge = session.scalar(
        select(RentCharge)
        .where(
            RentCharge.lease_id == lease.id,
            RentCharge.due_date >= start,
            RentCharge.due_date <= end,
        )
        .order_by(RentCharge.due_date, RentCharge.id)
        .limit(1)
    )
    if not charge:
        raise HTTPException(400, f"Для {MONTH_NAMES[month - 1]} {year} нет начисления аренды")
    return charge


def ensure_utility_line_target(session: Session, lease: Lease, line_id: int) -> UtilityBillLine:
    line = session.get(UtilityBillLine, line_id)
    if not line or line.lease_id != lease.id:
        raise HTTPException(404, "Коммунальный счёт не найден")
    return line


@app.post("/api/payment-receipts/manual")
def create_manual_payment(payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    lease = session.get(Lease, int(payload.get("lease_id") or 0))
    if not lease:
        raise HTTPException(404, "аренда не найдена")
    amount = float(payload.get("amount") or 0)
    if amount <= 0:
        raise HTTPException(400, "сумма платежа должна быть больше нуля")

    kind = (payload.get("kind") or "rent").strip()
    source = (payload.get("source") or "manual").strip() or "manual"
    paid_at = datetime.fromisoformat(payload["paid_at"]) if payload.get("paid_at") else utc_now()
    notes = (payload.get("notes") or "").strip()

    if kind == "rent":
        channel = (payload.get("channel") or "").strip()
        if channel not in {"ip", "personal", "expense_fund"}:
            raise HTTPException(400, "для аренды нужен канал ip, personal или expense_fund")
        target_charge_id = int(payload.get("rent_charge_id") or 0)
        target_month = int(payload.get("target_month") or 0)
        target_year = int(payload.get("target_year") or date.today().year)
        if target_charge_id:
            target_charge = session.get(RentCharge, target_charge_id)
            if not target_charge or target_charge.lease_id != lease.id:
                raise HTTPException(404, "Арендный месяц не найден")
            receipt = PaymentReceipt(
                lease_id=lease.id,
                rent_charge_id=target_charge.id,
                apartment_id=lease.apartment_id,
                amount=amount,
                channel=channel,
                paid_at=paid_at,
                source=source,
                status="accepted",
                notes=notes or "ручной платёж",
            )
            session.add(receipt)
            session.flush()
            recalculate_lease_balances(session, lease.id)
            sync_expense_fund_receipt(session, receipt)
            receipts = [receipt]
        elif target_month:
            target_charge = ensure_rent_charge_for_month(session, lease, target_year, target_month)
            receipt = PaymentReceipt(
                lease_id=lease.id,
                rent_charge_id=target_charge.id,
                apartment_id=lease.apartment_id,
                amount=amount,
                channel=channel,
                paid_at=paid_at,
                source=source,
                status="accepted",
                notes=notes or "ручной платёж",
            )
            session.add(receipt)
            session.flush()
            recalculate_lease_balances(session, lease.id)
            sync_expense_fund_receipt(session, receipt)
            receipts = [receipt]
        else:
            receipts = create_rent_receipts(
                session,
                lease,
                channel,
                amount,
                paid_at=paid_at,
                source=source,
                status="accepted",
                notes=notes or "ручной платёж",
                exact_only=False,
                cutoff=allocation_cutoff_date(session),
            )
            sync_expense_fund_receipts(session, receipts)
    elif kind == "utility":
        target_line_id = int(payload.get("utility_line_id") or 0)
        if target_line_id:
            target_line = ensure_utility_line_target(session, lease, target_line_id)
            receipt = PaymentReceipt(
                lease_id=lease.id,
                utility_line_id=target_line.id,
                apartment_id=lease.apartment_id,
                amount=amount,
                channel="utilities",
                paid_at=paid_at,
                source=source,
                status="accepted",
                notes=notes or "ручной платёж",
            )
            session.add(receipt)
            session.flush()
            recalculate_lease_balances(session, lease.id)
            receipts = [receipt]
        else:
            receipts = create_utility_receipts(
                session,
                lease,
                amount,
                paid_at=paid_at,
                source=source,
                status="accepted",
                notes=notes or "ручной платёж",
                exact_only=False,
            )
    else:
        raise HTTPException(400, "тип ручного платежа должен быть rent или utility")

    session.commit()
    return {
        "ok": True,
        "lease_id": lease.id,
        "created": [serialize_payment_receipt(receipt, session) for receipt in receipts],
    }


@app.patch("/api/payment-receipts/{receipt_id}")
def update_payment_receipt(receipt_id: int, payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    receipt = session.get(PaymentReceipt, receipt_id)
    if not receipt:
        raise HTTPException(404, "платёж не найден")
    old_lease_id = receipt.lease_id
    if "amount" in payload:
        amount = float(payload.get("amount") or 0)
        if amount <= 0:
            raise HTTPException(400, "сумма платежа должна быть больше нуля")
        receipt.amount = amount
    if payload.get("paid_at"):
        receipt.paid_at = datetime.fromisoformat(payload["paid_at"])
    if "notes" in payload:
        receipt.notes = str(payload.get("notes") or "")
    target_kind = (payload.get("target_kind") or "").strip()
    if target_kind:
        if target_kind == "rent":
            target_charge = session.get(RentCharge, int(payload.get("rent_charge_id") or 0))
            if not target_charge:
                raise HTTPException(404, "Арендный месяц не найден")
            channel = (payload.get("channel") or receipt.channel or "ip").strip()
            if channel not in {"ip", "personal", "expense_fund"}:
                raise HTTPException(400, "для аренды канал платежа должен быть ip, personal или expense_fund")
            receipt.rent_charge_id = target_charge.id
            receipt.utility_line_id = None
            receipt.lease_id = target_charge.lease_id
            receipt.apartment_id = target_charge.lease.apartment_id
            receipt.channel = channel
        elif target_kind == "utility":
            target_line = session.get(UtilityBillLine, int(payload.get("utility_line_id") or 0))
            if not target_line or not target_line.lease_id:
                raise HTTPException(404, "Коммунальный счёт не найден")
            receipt.rent_charge_id = None
            receipt.utility_line_id = target_line.id
            receipt.lease_id = target_line.lease_id
            receipt.apartment_id = target_line.apartment_id
            receipt.channel = "utilities"
        else:
            raise HTTPException(400, "неизвестная цель зачёта")
    elif payload.get("channel") and receipt.rent_charge_id:
        channel = payload.get("channel")
        if channel not in {"ip", "personal", "expense_fund"}:
            raise HTTPException(400, "для аренды канал платежа должен быть ip, personal или expense_fund")
        receipt.channel = channel
    if not target_kind and receipt.rent_charge_id and ("target_month" in payload or "target_year" in payload):
        lease = receipt.rent_charge.lease if receipt.rent_charge else session.get(Lease, receipt.lease_id)
        if not lease:
            raise HTTPException(400, "Не удалось определить аренду для этого платежа")
        target_month = int(payload.get("target_month") or 0)
        target_year = int(payload.get("target_year") or date.today().year)
        if not target_month:
            raise HTTPException(400, "Нужно выбрать месяц зачёта")
        target_charge = ensure_rent_charge_for_month(session, lease, target_year, target_month)
        receipt.rent_charge_id = target_charge.id
        receipt.lease_id = lease.id
        receipt.utility_line_id = None
        receipt.apartment_id = lease.apartment_id
    session.flush()
    sync_expense_fund_receipt(session, receipt)
    if receipt.status == "accepted":
        affected_ids = {item for item in [old_lease_id, receipt.lease_id] if item}
        for lease_id in affected_ids:
            recalculate_lease_balances(session, int(lease_id))
    session.commit()
    return serialize_payment_receipt(receipt, session)


@app.delete("/api/payment-receipts/{receipt_id}")
def delete_payment_receipt(receipt_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    receipt = session.get(PaymentReceipt, receipt_id)
    if not receipt:
        raise HTTPException(404, "платёж не найден")
    lease_id = receipt.lease_id
    status = receipt.status
    linked_expense = linked_expense_fund_record(session, receipt.id)
    if linked_expense:
        session.delete(linked_expense)
    session.delete(receipt)
    session.flush()
    if lease_id and status == "accepted":
        recalculate_lease_balances(session, lease_id)
    session.commit()
    return {"ok": True}


@app.get("/api/payment-receipts/suspicious")
def suspicious_receipts(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    receipts = session.scalars(
        select(PaymentReceipt)
        .where(PaymentReceipt.status == "suspicious")
        .order_by(PaymentReceipt.paid_at.desc(), PaymentReceipt.id.desc())
    ).all()
    return [serialize_payment_receipt(receipt, session) for receipt in receipts]


@app.post("/api/payment-receipts/{receipt_id}/moderate")
def moderate_payment_receipt(receipt_id: int, payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    receipt = session.get(PaymentReceipt, receipt_id)
    if not receipt:
        raise HTTPException(404, "платёж не найден")
    action = (payload.get("action") or "").strip()
    note = (payload.get("note") or "").strip()
    parsed = parse_receipt_details(receipt)
    channel_override = (payload.get("channel") or "").strip()
    channel = channel_override if channel_override in {"ip", "personal", "expense_fund"} else receipt.channel if receipt.channel in {"ip", "personal", "expense_fund"} else detect_receipt_channel(parsed)
    lease = session.get(Lease, receipt.lease_id) if receipt.lease_id else None
    if action in {"accept_rent", "accept_utility"} and not lease:
        raise HTTPException(400, "нужна аренда для этой операции")

    moderation_note = f"модерация owner{': ' + note if note else ''}"
    if action == "accept_rent":
        receipts = create_rent_receipts(
            session,
            lease,
            channel if channel in {"ip", "personal", "expense_fund"} else "personal",
            float(receipt.amount or 0),
            paid_at=receipt.paid_at,
            source=receipt.source,
            status="accepted",
            recipient_name=receipt.recipient_name,
            recipient_details=receipt.recipient_details,
            notes=moderation_note,
            file_path=receipt.file_path,
            exact_only=False,
            prefer_document_month=receipt.source == "telegram",
            cutoff=allocation_cutoff_date(session),
        )
        sync_expense_fund_receipts(session, receipts)
        receipt.status = "moderated"
    elif action == "accept_utility":
        create_utility_receipts(
            session,
            lease,
            float(receipt.amount or 0),
            paid_at=receipt.paid_at,
            source=receipt.source,
            status="accepted",
            recipient_name=receipt.recipient_name,
            recipient_details=receipt.recipient_details,
            notes=moderation_note,
            file_path=receipt.file_path,
            exact_only=False,
        )
        receipt.status = "moderated"
    elif action == "reject":
        receipt.status = "rejected"
    else:
        raise HTTPException(400, "неизвестное действие модерации")
    receipt.notes = "; ".join(part for part in [receipt.notes, moderation_note] if part)
    session.commit()
    return serialize_payment_receipt(receipt, session)


@app.post("/api/rent-charges/{charge_id}/defer")
def defer_rent_charge(charge_id: int, payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    charge = session.get(RentCharge, charge_id)
    if not charge:
        raise HTTPException(404, "Начисление не найдено")
    try:
        charge.deferral_until = resolve_deferral_until(payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    charge.deferral_note = payload.get("deferral_note") or ""
    update_rent_charge_status(charge)
    session.commit()
    return serialize_rent_charge(charge, session)


def resolve_deferral_until(payload: dict[str, Any], today: date | None = None) -> date:
    today = today or date.today()
    raw_days = payload.get("deferral_days", payload.get("days"))
    if raw_days not in (None, ""):
        try:
            days = int(raw_days)
        except (TypeError, ValueError) as exc:
            raise ValueError("Количество дней отсрочки должно быть целым числом") from exc
        if days <= 0:
            raise ValueError("Количество дней отсрочки должно быть больше нуля")
        return today + timedelta(days=days)

    if payload.get("deferral_until"):
        return parse_date(payload.get("deferral_until"))

    raise ValueError("Укажите количество дней отсрочки")


@app.get("/api/meters")
def list_meters(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    return [serialize_meter(meter) for meter in session.scalars(select(Meter).order_by(Meter.object_id, Meter.scope, Meter.name)).all()]


@app.post("/api/meter-readings")
def save_meter_reading(payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    meter = session.get(Meter, int(payload["meter_id"]))
    if not meter:
        raise HTTPException(404, "Счётчик не найден")
    reading_date = parse_date(payload.get("reading_date"), date.today())
    existing = session.scalar(select(MeterReading).where(MeterReading.meter_id == meter.id, MeterReading.reading_date == reading_date))
    if existing:
        existing.value = float(payload["value"])
        existing.note = payload.get("note") or ""
        reading = existing
    else:
        reading = MeterReading(
            meter_id=meter.id,
            reading_date=reading_date,
            value=float(payload["value"]),
            note=payload.get("note") or "",
        )
        session.add(reading)
    session.commit()
    return {
        "id": reading.id,
        "meter": meter.name,
        "reading_date": reading.reading_date.isoformat(),
        "value": reading.value,
        "note": reading.note,
    }


def upsert_meter_reading(session: Session, meter_id: int, reading_date: date, value: float, note: str = "") -> MeterReading:
    meter = session.get(Meter, meter_id)
    if not meter:
        raise HTTPException(404, f"Счётчик {meter_id} не найден")
    existing = session.scalar(select(MeterReading).where(MeterReading.meter_id == meter.id, MeterReading.reading_date == reading_date))
    if existing:
        existing.value = value
        existing.note = note
        return existing
    reading = MeterReading(meter_id=meter.id, reading_date=reading_date, value=value, note=note)
    session.add(reading)
    return reading


@app.post("/api/meter-readings/batch")
def save_meter_readings_batch(payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    reading_date = parse_date(payload.get("reading_date"), date.today())
    readings = payload.get("readings") or []
    saved: list[MeterReading] = []
    for item in readings:
        raw_value = item.get("value")
        if raw_value in (None, ""):
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "Показание должно быть числом") from exc
        meter_id = int(item["meter_id"])
        saved.append(upsert_meter_reading(session, meter_id, reading_date, value, item.get("note") or "быстрая передача"))
    session.commit()
    return {
        "saved": len(saved),
        "reading_date": reading_date.isoformat(),
    }


@app.get("/api/tariffs")
def list_tariffs(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    tariffs = session.scalars(select(Tariff).order_by(Tariff.service_id, Tariff.starts_on.desc())).all()
    return [
        {
            "id": tariff.id,
            "service_id": tariff.service_id,
            "kind": tariff.service.kind,
            "service": tariff.service.name,
            "object": tariff.service.object.name,
            "starts_on": tariff.starts_on.isoformat(),
            "name": tariff.name,
            "tiers": json.loads(tariff.tiers_json),
        }
        for tariff in tariffs
    ]


def parse_tiers(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return value
    result = []
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("Тарифные ступени пустые")
    normalized = re.sub(r",\s*(?=[\"'])", "; ", raw.replace("\n", ";"))
    for chunk in normalized.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" in chunk or "=" in chunk:
            separator = ":" if ":" in chunk else "="
            limit_raw, price_raw = [part.strip().strip('"').strip("'") for part in chunk.split(separator, 1)]
            limit = None if limit_raw in {"*", "∞", "inf", "null"} else float(limit_raw.replace(" ", ""))
        else:
            limit = None
            price_raw = chunk.strip().strip('"').strip("'")
        price = float(price_raw.replace(" ", "").replace(",", "."))
        result.append({"limit": limit, "price": price})
    if not result or result[-1]["limit"] is not None:
        result.append({"limit": None, "price": result[-1]["price"]})
    return result


@app.post("/api/tariffs")
def create_tariff(payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        tiers = parse_tiers(payload.get("tiers"))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    tariff = Tariff(
        service_id=int(payload["service_id"]),
        starts_on=parse_date(payload.get("starts_on"), date.today()),
        name=payload.get("name") or "Тариф",
        tiers_json=json.dumps(tiers, ensure_ascii=False),
    )
    session.add(tariff)
    session.commit()
    session.refresh(tariff)
    return {
        "id": tariff.id,
        "service_id": tariff.service_id,
        "starts_on": tariff.starts_on.isoformat(),
        "name": tariff.name,
        "tiers": tiers,
    }


@app.get("/api/utility-bills")
def list_utility_bills(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    bills = session.scalars(select(UtilityBill).order_by(UtilityBill.created_at.desc(), UtilityBill.id.desc())).all()
    return [serialize_bill(bill, session) for bill in bills]


@app.get("/api/utilities/timeline")
def utility_timeline(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    object_readings = session.scalars(
        select(MeterReading).join(Meter).where(Meter.scope == "object", Meter.active.is_(True)).order_by(MeterReading.reading_date.desc(), MeterReading.id.desc())
    ).all()
    for reading in object_readings:
        meter = reading.meter
        service = meter.service
        events.append(
            {
                "kind": "reading",
                "sort_at": f"{reading.reading_date.isoformat()}T00:00:00",
                "date": reading.reading_date.isoformat(),
                "object": service.object.name,
                "service": service.name,
                "service_id": service.id,
                "title": "Общедомовые показания",
                "detail": f"{meter.name}: {reading.value}",
            }
        )

    bills = session.scalars(select(UtilityBill).order_by(UtilityBill.period_end.desc(), UtilityBill.id.desc())).all()
    for bill in bills:
        events.append(
            {
                "kind": "bill",
                "sort_at": f"{bill.period_end.isoformat()}T12:00:00",
                "date": bill.period_end.isoformat(),
                "object": bill.service.object.name,
                "service": bill.service.name,
                "service_id": bill.service_id,
                "bill_id": bill.id,
                "title": f"{bill.period_start:%d.%m.%Y} -> {bill.period_end:%d.%m.%Y} ({(bill.period_end - bill.period_start).days} дн.)",
                "detail": f"{(bill.period_end - bill.period_start).days} дн., жильцам {money_text(sum(line.total_amount for line in bill.lines))}, по дому {money_text(bill.total_cost)}",
                "status": bill.status,
                "provider_paid": bill.provider_paid,
            }
        )
        if bill.provider_paid_at:
            events.append(
                {
                    "kind": "provider_paid",
                    "sort_at": bill.provider_paid_at.isoformat(),
                    "date": bill.provider_paid_at.date().isoformat(),
                    "object": bill.service.object.name,
                    "service": bill.service.name,
                    "service_id": bill.service_id,
                    "bill_id": bill.id,
                    "title": "Поставщик оплачен",
                    "detail": f"Период {format_full_date(bill.period_start)} -> {format_full_date(bill.period_end)}",
                    "status": bill.status,
                    "provider_paid": True,
                }
            )

    utility_receipts = session.scalars(
        select(PaymentReceipt).where(PaymentReceipt.utility_line_id.is_not(None)).order_by(PaymentReceipt.paid_at.desc(), PaymentReceipt.id.desc())
    ).all()
    for receipt in utility_receipts:
        line = session.get(UtilityBillLine, receipt.utility_line_id) if receipt.utility_line_id else None
        if not line:
            continue
        bill = line.bill
        events.append(
            {
                "kind": "resident_payment",
                "sort_at": receipt.paid_at.isoformat(),
                "date": receipt.paid_at.date().isoformat(),
                "object": bill.service.object.name,
                "service": bill.service.name,
                "service_id": bill.service_id,
                "bill_id": bill.id,
                "title": f"Оплата жильца: {line.apartment.name}",
                "detail": f"{line.lease.tenant.full_name if line.lease else ''} — {money_text(receipt.amount)}",
                "status": receipt.status,
                "provider_paid": bill.provider_paid,
            }
        )

    events.sort(key=lambda item: item["sort_at"], reverse=True)
    return events


@app.post("/api/utility-bills/calculate")
def create_utility_bill(payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        existing = session.scalar(
            select(UtilityBill).where(
                UtilityBill.service_id == int(payload["service_id"]),
                UtilityBill.period_start == parse_date(payload.get("period_start")),
                UtilityBill.period_end == parse_date(payload.get("period_end")),
                UtilityBill.status == "draft",
            )
        )
        if existing:
            raise ValueError("Черновик за этот период уже есть. Удалите его и пересоздайте заново, если нужен новый расчёт.")
        bill, warnings = calculate_utility_bill(
            session=session,
            service_id=int(payload["service_id"]),
            period_start=parse_date(payload.get("period_start")),
            period_end=parse_date(payload.get("period_end")),
            allow_estimate=bool(payload.get("allow_estimate")),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    session.add(bill)
    session.commit()
    session.refresh(bill)
    result = serialize_bill(bill)
    result["warnings"] = warnings
    return result


@app.delete("/api/utility-bills/{bill_id}")
def delete_utility_bill(bill_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    bill = session.get(UtilityBill, bill_id)
    if not bill:
        raise HTTPException(404, "Коммунальный счёт не найден")
    if bill.status != "draft":
        raise HTTPException(400, "Удалять можно только черновики")
    session.delete(bill)
    session.commit()
    return {"ok": True, "bill_id": bill_id}


@app.post("/api/utility-bills/{bill_id}/issue")
def issue_utility_bill(bill_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    bill = session.get(UtilityBill, bill_id)
    if not bill:
        raise HTTPException(404, "Коммунальный счёт не найден")
    previews = utility_issue_targets(session, bill)
    due = resident_due_date(bill.service)
    bill.status = "issued"
    bill.due_date = due
    for line in bill.lines:
        line.status = "issued"
        line.issued_at = utc_now()
        line.due_date = due
    sent = 0
    skipped_unlinked = 0
    for preview in previews:
        lease = session.get(Lease, preview["lease_id"])
        if not lease:
            continue
        if not preview["linked"]:
            skipped_unlinked += 1
            continue
        send_tenant_message(
            session,
            lease,
            "message_utility_bill",
            line=session.get(UtilityBillLine, preview["line_ids"][0]) if preview["line_ids"] else None,
            note="utility-issue",
            utility_lines_override=[line for line_id in preview["all_line_ids"] if (line := session.get(UtilityBillLine, line_id))],
            utility_due_date_override=due,
        )
        sent += 1
    session.commit()
    session.refresh(bill)
    payload = serialize_bill(bill)
    payload["sent"] = sent
    payload["skipped_unlinked"] = skipped_unlinked
    return payload


@app.get("/api/utility-bills/{bill_id}/issue-preview")
def preview_issue_utility_bill(bill_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    bill = session.get(UtilityBill, bill_id)
    if not bill:
        raise HTTPException(404, "Коммунальный счёт не найден")
    return {
        "bill_id": bill.id,
        "object": bill.service.object.name,
        "service": bill.service.name,
        "period_label": f"{bill.period_start:%d.%m.%Y} -> {bill.period_end:%d.%m.%Y}",
        "due_date": resident_due_date(bill.service).isoformat(),
        "targets": utility_issue_targets(session, bill),
    }


@app.post("/api/utility-bills/{bill_id}/provider-paid")
def mark_provider_paid(bill_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    bill = session.get(UtilityBill, bill_id)
    if not bill:
        raise HTTPException(404, "Коммунальный счёт не найден")
    bill.provider_paid = True
    bill.provider_paid_at = utc_now()
    session.commit()
    return serialize_bill(bill)


@app.post("/api/utility-lines/{line_id}/payments")
def add_utility_payment(line_id: int, payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    line = session.get(UtilityBillLine, line_id)
    if not line:
        raise HTTPException(404, "строка коммуналки не найдена")
    amount = float(payload.get("amount") or 0)
    if amount <= 0:
        raise HTTPException(400, "сумма платежа должна быть больше нуля")
    if not line.lease:
        raise HTTPException(400, "у этой строки нет привязанной аренды")
    create_utility_receipts(
        session,
        line.lease,
        amount,
        paid_at=datetime.fromisoformat(payload["paid_at"]) if payload.get("paid_at") else utc_now(),
        source=payload.get("source") or "manual",
        status=payload.get("status") or "accepted",
        recipient_name=payload.get("recipient_name") or "",
        recipient_details=payload.get("recipient_details") or "",
        notes=payload.get("notes") or "",
        exact_only=False,
    )
    session.commit()
    return serialize_bill_line(line, session)


@app.get("/api/expenses")
def list_expenses(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    expenses = session.scalars(select(Expense).order_by(Expense.expense_date.desc(), Expense.id.desc())).all()
    return [serialize_expense(expense) for expense in expenses]


@app.post("/api/expenses")
def create_expense(payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    expense = Expense(
        expense_date=parse_date(payload.get("expense_date"), date.today()),
        object_id=int(payload["object_id"]) if payload.get("object_id") else None,
        apartment_id=int(payload["apartment_id"]) if payload.get("apartment_id") else None,
        category=payload.get("category") or "",
        amount=float(payload.get("amount") or 0),
        source_funds=payload.get("source_funds") or "personal",
        payment_method=payload.get("payment_method") or "",
        description=payload.get("description") or "",
        compensation_status="pending" if payload.get("source_funds", "personal") == "personal" else "not_required",
        file_path=payload.get("file_path") or "",
        notes=payload.get("notes") or "",
    )
    if expense.amount <= 0:
        raise HTTPException(400, "Сумма должна быть больше нуля")
    session.add(expense)
    session.commit()
    session.refresh(expense)
    return serialize_expense(expense)


@app.post("/api/expenses/{expense_id}/compensate")
def compensate_expense(expense_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    expense = session.get(Expense, expense_id)
    if not expense:
        raise HTTPException(404, "Расход не найден")
    expense.compensation_status = "compensated"
    expense.compensated_at = utc_now()
    session.commit()
    return serialize_expense(expense)


def setup_sheet(wb: Workbook, title: str, headers: list[str]):
    if wb.active.max_row == 1 and wb.active.max_column == 1 and wb.active["A1"].value is None and wb.active.title == "Sheet":
        ws = wb.active
        ws.title = title
    else:
        ws = wb.create_sheet(title)
    ws.append(headers)
    fill = PatternFill("solid", fgColor="E8EEF8")
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = fill
    return ws


def workbook_response(wb: Workbook, filename: str) -> StreamingResponse:
    stream = BytesIO()
    wb.save(stream)
    stream.seek(0)
    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


STATUS_LABELS = {
    "pending": "ожидается",
    "overdue": "просрочено",
    "partial": "частично оплачено",
    "paid": "оплачено",
    "paid_ahead": "оплачено вперёд",
    "deferred": "отсрочка",
    "draft": "черновик",
    "issued": "выставлено",
    "cancelled": "отменено",
    "not_required": "не требуется",
    "compensated": "компенсировано",
}


def report_status_label(status: str, due_date: date | None = None, today: date | None = None) -> str:
    label = STATUS_LABELS.get(status, status)
    today = today or date.today()
    if status == "overdue" and due_date:
        days = max((today - due_date).days, 0)
        return f"{label}, {days} дн."
    return label


def accepted_receipts_for_charge(charge: RentCharge, channel: str) -> list[PaymentReceipt]:
    return [receipt for receipt in charge.receipts if receipt.status == "accepted" and receipt.channel == channel]


def receipt_amounts_text(receipts: list[PaymentReceipt]) -> str:
    if not receipts:
        return ""
    return "; ".join(f"{receipt.paid_at:%d.%m.%Y} — {money_text(float(receipt.amount or 0))}" for receipt in sorted(receipts, key=lambda item: (item.paid_at, item.id)))


def receipt_senders_text(receipts: list[PaymentReceipt]) -> str:
    senders = []
    for receipt in receipts:
        sender = receipt_sender_name(parse_receipt_details(receipt))
        if sender not in senders:
            senders.append(sender)
    return ", ".join(senders)


def apartment_month_state(apartment: Apartment, start_date: date, end_date: date) -> dict[str, Any]:
    total_days = (end_date - start_date).days + 1
    occupied_days: set[date] = set()
    overlaps: list[Lease] = []
    for lease in sorted(apartment.leases, key=lambda item: (item.start_date, item.id)):
        overlap_start = max(lease.start_date, start_date)
        overlap_end = min(lease.end_date or end_date, end_date)
        if overlap_start > overlap_end:
            continue
        overlaps.append(lease)
        for day_index in range((overlap_end - overlap_start).days + 1):
            occupied_days.add(overlap_start + timedelta(days=day_index))

    tenant_names: list[str] = []
    for lease in overlaps:
        if lease.tenant.full_name not in tenant_names:
            tenant_names.append(lease.tenant.full_name)

    vacant_days = max(total_days - len(occupied_days), 0)
    flags: list[str] = []
    if not overlaps:
        flags.append("пустует весь месяц")
    else:
        if len(tenant_names) > 1:
            flags.append("смена жильца")
        if vacant_days > 0:
            flags.append(f"пустует {vacant_days} дн.")
        if not flags:
            flags.append("без смены")
    return {
        "state": "; ".join(flags),
        "tenant_names": " -> ".join(tenant_names) if tenant_names else "нет жильца",
        "vacant_days": vacant_days,
        "changed": len(tenant_names) > 1,
        "vacant_full": not overlaps,
    }


@app.get("/api/reports/rent.xlsx")
def rent_report(start: str | None = None, end: str | None = None, session: Session = Depends(get_session)) -> StreamingResponse:
    start_date, end_date = current_month_range()
    if start:
        start_date = parse_date(start)
    if end:
        end_date = parse_date(end)
    charges = session.scalars(
        select(RentCharge).where(RentCharge.due_date >= start_date, RentCharge.due_date <= end_date).order_by(RentCharge.due_date)
    ).all()
    wb = Workbook()
    ws = setup_sheet(
        wb,
        "Аренда",
        [
            "Дата",
            "Период",
            "Объект",
            "Квартира",
            "Жилец",
            "Статус квартиры в периоде",
            "ИП начислено",
            "ИП оплачено",
            "ИП платежи",
            "ИП отправители",
            "Личный начислено",
            "Личный оплачено",
            "Долг",
            "Статус",
        ],
    )
    for charge in charges:
        data = serialize_rent_charge(charge)
        ip_receipts = accepted_receipts_for_charge(charge, "ip")
        apartment_state = apartment_month_state(charge.lease.apartment, charge.period_start, charge.period_end)
        ws.append(
            [
                data["due_date"],
                f'{data["period_start"]} - {data["period_end"]}',
                data["object"],
                data["apartment"],
                data["tenant"],
                apartment_state["state"],
                data["ip_due"],
                data["ip_paid"],
                receipt_amounts_text(ip_receipts),
                receipt_senders_text(ip_receipts),
                data["personal_due"],
                data["personal_paid"],
                data["debt"],
                report_status_label(data["status"], charge.due_date),
            ]
        )
    return workbook_response(wb, f"rent-{start_date}-{end_date}.xlsx")


@app.get("/api/reports/utilities.xlsx")
def utilities_report(start: str | None = None, end: str | None = None, session: Session = Depends(get_session)) -> StreamingResponse:
    start_date, end_date = current_month_range()
    if start:
        start_date = parse_date(start)
    if end:
        end_date = parse_date(end)
    bills = session.scalars(
        select(UtilityBill)
        .where(UtilityBill.period_start >= start_date, UtilityBill.period_end <= end_date)
        .order_by(UtilityBill.period_start)
    ).all()
    wb = Workbook()
    ws = setup_sheet(
        wb,
        "Коммуналка",
        ["Период", "Объект", "Услуга", "Квартира", "Жилец", "Личный расход", "ОДН", "Сумма", "Оплачено", "Долг", "Статус"],
    )
    for bill in bills:
        for line in bill.lines:
            data = serialize_bill_line(line)
            ws.append(
                [
                    f"{bill.period_start} - {bill.period_end}",
                    bill.service.object.name,
                    bill.service.name,
                    data["apartment"],
                    data["tenant"],
                    data["personal_consumption"],
                    data["odn_consumption"],
                    data["total_amount"],
                    data["paid_amount"],
                    data["debt"],
                    report_status_label(data["status"], line.due_date),
                ]
            )
    return workbook_response(wb, f"utilities-{start_date}-{end_date}.xlsx")


@app.get("/api/reports/debts.xlsx")
def debts_report(session: Session = Depends(get_session)) -> StreamingResponse:
    wb = Workbook()
    ws = setup_sheet(wb, "Долги", ["Тип", "Дата", "Объект", "Квартира", "Жилец", "Сумма", "Комментарий"])
    for charge in session.scalars(select(RentCharge).order_by(RentCharge.due_date)).all():
        data = serialize_rent_charge(charge)
        if data["debt"] > 0 and data["status"] in {"overdue", "partial", "deferred"}:
            ws.append(["Аренда", data["due_date"], data["object"], data["apartment"], data["tenant"], data["debt"], report_status_label(data["status"], charge.due_date)])
    for line in session.scalars(select(UtilityBillLine)).all():
        data = serialize_bill_line(line)
        if data["debt"] > 0 and data["status"] in {"overdue", "partial", "issued"}:
            ws.append(["Коммуналка", data["due_date"], line.bill.service.object.name, data["apartment"], data["tenant"], data["debt"], report_status_label(data["status"], line.due_date)])
    return workbook_response(wb, "debts.xlsx")


@app.get("/api/reports/expenses.xlsx")
def expenses_report(start: str | None = None, end: str | None = None, session: Session = Depends(get_session)) -> StreamingResponse:
    start_date, end_date = current_month_range()
    if start:
        start_date = parse_date(start)
    if end:
        end_date = parse_date(end)
    expenses = session.scalars(
        select(Expense).where(Expense.expense_date >= start_date, Expense.expense_date <= end_date).order_by(Expense.expense_date)
    ).all()
    wb = Workbook()
    ws = setup_sheet(wb, "Расходы", ["Дата", "Объект", "Квартира", "Категория", "Сумма", "Источник", "Способ", "Компенсация", "Описание"])
    for expense in expenses:
        data = serialize_expense(expense)
        ws.append(
            [
                data["expense_date"],
                data["object"],
                data["apartment"],
                data["category"],
                data["amount"],
                data["source_funds"],
                data["payment_method"],
                report_status_label(data["compensation_status"]),
                data["description"],
            ]
        )
    return workbook_response(wb, f"expenses-{start_date}-{end_date}.xlsx")


@app.get("/api/reports/monthly.xlsx")
def monthly_report(year: int, month: int, session: Session = Depends(get_session)) -> StreamingResponse:
    try:
        start_date, end_date = month_range(year, month)
    except ValueError as exc:
        raise HTTPException(400, "Некорректный месяц") from exc

    summary = monthly_report_status(session, year, month)
    rent_charges = session.scalars(
        select(RentCharge).where(RentCharge.due_date >= start_date, RentCharge.due_date <= end_date).order_by(RentCharge.due_date)
    ).all()
    bills = session.scalars(
        select(UtilityBill)
        .where(UtilityBill.period_start >= start_date, UtilityBill.period_start <= end_date)
        .order_by(UtilityBill.period_start)
    ).all()
    apartments = session.scalars(select(Apartment).where(Apartment.active.is_(True)).order_by(Apartment.object_id, Apartment.sort_order, Apartment.name)).all()

    utility_lines = [line for bill in bills for line in bill.lines]
    utility_debt_by_apartment: dict[int, float] = {}
    for line in utility_lines:
        utility_debt_by_apartment[line.apartment_id] = utility_debt_by_apartment.get(line.apartment_id, 0.0) + max(0.0, float(line.total_amount or 0) - float(line.paid_amount or 0))

    charge_by_apartment = {charge.lease.apartment_id: charge for charge in rent_charges}
    apartment_rows: list[dict[str, Any]] = []
    for apartment in apartments:
        state = apartment_month_state(apartment, start_date, end_date)
        charge = charge_by_apartment.get(apartment.id)
        utility_debt = money(utility_debt_by_apartment.get(apartment.id, 0.0))
        apartment_rows.append(
            {
                "object": apartment.object.name,
                "apartment": apartment.name,
                "tenant_names": state["tenant_names"],
                "state": state["state"],
                "changed": state["changed"],
                "vacant_full": state["vacant_full"],
                "rent_status": report_status_label(charge.status, charge.due_date) if charge else "нет начисления",
                "rent_debt": money(max(0.0, float(charge.ip_due + charge.personal_due) - float(charge.ip_paid + charge.personal_paid))) if charge else 0.0,
                "utility_debt": utility_debt,
            }
        )

    occupied_rows = [row for row in apartment_rows if not row["vacant_full"]]
    settled_rows = [row for row in occupied_rows if row["rent_debt"] <= EPS and row["utility_debt"] <= EPS]
    ip_receipts = [receipt for charge in rent_charges for receipt in accepted_receipts_for_charge(charge, "ip")]
    ip_income = money(sum(float(receipt.amount or 0) for receipt in ip_receipts))

    wb = Workbook()
    ws = setup_sheet(wb, "Сводка", ["Показатель", "Значение"])
    ws.append(["Период", f"{start_date} - {end_date}"])
    ws.append(["Статус", summary["label"]])
    ws.append(["Всего проблем", summary["issue_count"]])
    ws.append(["Критических", summary["danger_count"]])
    ws.append(["Предупреждений", summary["warning_count"]])
    ws.append(["Квартир всего", len(apartment_rows)])
    ws.append(["Занятых квартир", len(occupied_rows)])
    ws.append(["Полностью рассчитались", len(settled_rows)])
    ws.append(["Квартир с долгом по аренде", sum(1 for row in occupied_rows if row["rent_debt"] > EPS)])
    ws.append(["Квартир с долгом по коммуналке", sum(1 for row in occupied_rows if row["utility_debt"] > EPS)])
    ws.append(["Квартир пустовало весь месяц", sum(1 for row in apartment_rows if row["vacant_full"])])
    ws.append(["Квартир со сменой жильца", sum(1 for row in apartment_rows if row["changed"])])
    ws.append(["Поступления на ИП, зачтённые за месяц", ip_income])

    issues_ws = setup_sheet(wb, "Проблемы", ["Важность", "Проблема", "Количество", "Комментарий"])
    for issue in summary["issues"]:
        issues_ws.append([issue["severity"], issue["title"], issue["count"], issue["detail"]])

    apartments_ws = setup_sheet(
        wb,
        "Квартиры",
        ["Объект", "Квартира", "Жильцы за месяц", "Статус месяца", "Аренда", "Долг аренды", "Долг коммуналки"],
    )
    for row in apartment_rows:
        apartments_ws.append(
            [
                row["object"],
                row["apartment"],
                row["tenant_names"],
                row["state"],
                row["rent_status"],
                row["rent_debt"],
                row["utility_debt"],
            ]
        )

    rent_ws = setup_sheet(
        wb,
        "Аренда",
        [
            "Дата",
            "Период",
            "Объект",
            "Квартира",
            "Жилец",
            "Статус квартиры в месяце",
            "ИП начислено",
            "ИП оплачено",
            "ИП платежи",
            "ИП отправители",
            "Личный начислено",
            "Личный оплачено",
            "Долг",
            "Статус",
        ],
    )
    for charge in rent_charges:
        data = serialize_rent_charge(charge)
        ip_charge_receipts = accepted_receipts_for_charge(charge, "ip")
        apartment_state = apartment_month_state(charge.lease.apartment, start_date, end_date)
        rent_ws.append(
            [
                data["due_date"],
                f'{data["period_start"]} - {data["period_end"]}',
                data["object"],
                data["apartment"],
                data["tenant"],
                apartment_state["state"],
                data["ip_due"],
                data["ip_paid"],
                receipt_amounts_text(ip_charge_receipts),
                receipt_senders_text(ip_charge_receipts),
                data["personal_due"],
                data["personal_paid"],
                data["debt"],
                report_status_label(data["status"], charge.due_date),
            ]
        )

    ip_ws = setup_sheet(
        wb,
        "ИП платежи",
        ["Объект", "Квартира", "Жилец", "Месяц зачёта", "Оплачен по чеку", "Добавлен в систему", "Сумма", "Отправитель", "Источник"],
    )
    for charge in rent_charges:
        for receipt in accepted_receipts_for_charge(charge, "ip"):
            parsed = parse_receipt_details(receipt)
            ip_ws.append(
                [
                    charge.lease.apartment.object.name,
                    charge.lease.apartment.name,
                    charge.lease.tenant.full_name,
                    debt_month_heading(charge.due_date),
                    receipt.paid_at.strftime("%d.%m.%Y %H:%M"),
                    receipt.created_at.strftime("%d.%m.%Y %H:%M"),
                    money(receipt.amount),
                    receipt_sender_name(parsed),
                    payment_source_label(receipt),
                ]
            )

    utilities_ws = setup_sheet(
        wb,
        "Коммуналка",
        ["Период", "Объект", "Услуга", "Квартира", "Жилец", "Сумма", "Оплачено", "Долг", "Статус", "Поставщик оплачен"],
    )
    for bill in bills:
        for line in bill.lines:
            data = serialize_bill_line(line)
            utilities_ws.append(
                [
                    f"{bill.period_start} - {bill.period_end}",
                    bill.service.object.name,
                    bill.service.name,
                    data["apartment"],
                    data["tenant"],
                    data["total_amount"],
                    data["paid_amount"],
                    data["debt"],
                    report_status_label(data["status"], line.due_date),
                    "да" if bill.provider_paid else "нет",
                ]
            )

    expenses_ws = setup_sheet(wb, "Расходы", ["Дата", "Объект", "Квартира", "Категория", "Сумма", "Источник", "Способ", "Компенсация", "Описание"])
    expenses = session.scalars(
        select(Expense).where(Expense.expense_date >= start_date, Expense.expense_date <= end_date).order_by(Expense.expense_date)
    ).all()
    for expense in expenses:
        data = serialize_expense(expense)
        expenses_ws.append(
            [
                data["expense_date"],
                data["object"],
                data["apartment"],
                data["category"],
                data["amount"],
                data["source_funds"],
                data["payment_method"],
                report_status_label(data["compensation_status"]),
                data["description"],
            ]
        )

    filename = f"monthly-{year}-{month:02d}.xlsx"
    return workbook_response(wb, filename)


@app.get("/api/reports/history.xlsx")
def history_report(
    apartment_id: int | None = None,
    tenant_id: int | None = None,
    session: Session = Depends(get_session),
) -> StreamingResponse:
    wb = Workbook()
    ws = setup_sheet(wb, "История", ["Тип", "Дата", "Объект", "Квартира", "Жилец", "Сумма", "Статус", "Комментарий"])

    lease_ids = []
    if apartment_id:
        lease_ids = [lease.id for lease in session.scalars(select(Lease).where(Lease.apartment_id == apartment_id)).all()]
    if tenant_id:
        lease_ids = [lease.id for lease in session.scalars(select(Lease).where(Lease.tenant_id == tenant_id)).all()]
    if not lease_ids:
        lease_ids = [lease.id for lease in session.scalars(select(Lease)).all()]

    for charge in session.scalars(select(RentCharge).where(RentCharge.lease_id.in_(lease_ids)).order_by(RentCharge.due_date)).all():
        data = serialize_rent_charge(charge)
        ws.append(["Аренда", data["due_date"], data["object"], data["apartment"], data["tenant"], data["total_due"], report_status_label(data["status"], charge.due_date), ""])

    for line in session.scalars(select(UtilityBillLine).where(UtilityBillLine.lease_id.in_(lease_ids))).all():
        data = serialize_bill_line(line)
        ws.append(["Коммуналка", data["due_date"], line.bill.service.object.name, data["apartment"], data["tenant"], data["total_amount"], report_status_label(data["status"], line.due_date), ""])

    return workbook_response(wb, "history.xlsx")
