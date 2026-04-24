from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any

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
    "ip_recipient_name": "",
    "ip_recipient_account": "",
    "ip_recipient_bik": "",
    "personal_recipient_name": "",
    "personal_recipient_phone": "",
    "personal_recipient_bank": "",
    "message_rent_due": "Здравствуйте. Напоминаю: по квартире {apartment} сегодня ожидается оплата аренды. ИП: {ip_due}. Перевод: {personal_due}. Итого: {total_due}.",
    "message_rent_overdue": "Здравствуйте. По квартире {apartment} сейчас просрочена аренда. Долг: {debt}. ИП: {ip_due}. Перевод: {personal_due}.",
    "message_utility_bill": "Здравствуйте. Коммуналка по квартире {apartment} за период {period} составляет {utility_total}. Срок оплаты до {utility_due_date}.",
    "message_receipt_received": "Чек получил и сохранил. Если всё совпало, я это отмечу. Если нет, передам владельцу на ручную проверку.",
    "message_receipt_review": "Чек получил, но там есть вопросы. Я уже отправил его владельцу на проверку.",
    "message_owner_receipt_alert": "Новый чек от {tenant_name}. Квартира: {apartment}. Сумма: {amount}. Канал: {channel}. Статус: {receipt_status}. {receipt_summary}",
}
SECRET_SETTINGS = {
    "telegram_bot_token": "",
    "telegram_webhook_secret": "",
}
ALL_SETTINGS = {**DEFAULT_SETTINGS, **SECRET_SETTINGS}
INTERNAL_SETTINGS = {
    "telegram_tenant_links": "{}",
}
BOOLEAN_SETTINGS = {"notifications_enabled"}

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
    if key not in ALL_SETTINGS and key not in INTERNAL_SETTINGS:
        return ""
    row = session.get(AppSetting, key)
    if row:
        return row.value
    if key in ALL_SETTINGS:
        return str(ALL_SETTINGS[key])
    return str(INTERNAL_SETTINGS[key])


def get_settings(session: Session) -> dict[str, str | bool]:
    settings: dict[str, str | bool] = {}
    for key in DEFAULT_SETTINGS:
        raw = get_setting_value(session, key)
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


def maybe_link_tenant_chat(session: Session, message: dict[str, Any]) -> Lease | None:
    sender = message.get("from") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
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
    return {
        "linked": bool(chat_id),
        "auto_enabled": auto_enabled,
        "eligible_auto": eligible and auto_enabled,
        "cutoff_date": cutoff.isoformat(),
        "block_reason": block_reason,
        "latest": serialize_message_log(latest),
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


def serialize_lease(lease: Lease) -> dict[str, Any]:
    return {
        "id": lease.id,
        "apartment_id": lease.apartment_id,
        "apartment": lease.apartment.name,
        "object": lease.apartment.object.name,
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
        "notes": lease.notes,
        "active": lease.active,
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
    return {
        "id": receipt.id,
        "lease_id": receipt.lease_id,
        "rent_charge_id": receipt.rent_charge_id,
        "utility_line_id": receipt.utility_line_id,
        "apartment": apartment,
        "tenant": tenant,
        "amount": money(receipt.amount),
        "channel": receipt.channel,
        "status": receipt.status,
        "paid_at": receipt.paid_at.isoformat(),
        "source": receipt.source,
        "created_at": receipt.created_at.isoformat(),
        "recipient_name": receipt.recipient_name,
        "notes": receipt.notes or "",
        "file_path": receipt.file_path or "",
        "target_label": target_label,
        "target_month": target_month,
        "parsed": parsed,
    }


def serialize_rent_charge(charge: RentCharge, session: Session | None = None) -> dict[str, Any]:
    update_rent_charge_status(charge)
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
        "reminder": reminder_meta(session, charge.lease, charge.due_date, rent_charge_id=charge.id),
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
        "reminder": reminder_meta(session, line.lease, line.due_date, utility_line_id=line.id) if line.lease else None,
    }


def serialize_bill(bill: UtilityBill, session: Session | None = None) -> dict[str, Any]:
    return {
        "id": bill.id,
        "service_id": bill.service_id,
        "service": bill.service.name,
        "object": bill.service.object.name,
        "period_start": bill.period_start.isoformat(),
        "period_end": bill.period_end.isoformat(),
        "status": bill.status,
        "total_consumption": bill.total_consumption,
        "apartment_consumption": bill.apartment_consumption,
        "odn_consumption": bill.odn_consumption,
        "total_cost": bill.total_cost,
        "average_unit_price": bill.average_unit_price,
        "due_date": bill.due_date.isoformat() if bill.due_date else None,
        "is_forecast": bill.is_forecast,
        "provider_paid": bill.provider_paid,
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
        "leases": [serialize_lease(lease) for lease in session.scalars(select(Lease).order_by(Lease.active.desc(), Lease.start_date.desc())).all()],
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
        .where(
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
    charges = session.scalars(select(RentCharge).order_by(RentCharge.due_date)).all()
    for charge in charges:
        update_rent_charge_status(charge, today)
    utility_lines = session.scalars(select(UtilityBillLine)).all()
    for line in utility_lines:
        update_utility_line_status(line, today)

    overdue_rent = [serialize_rent_charge(charge, session) for charge in charges if charge.status == "overdue"]
    partial_rent = [serialize_rent_charge(charge, session) for charge in charges if charge.status == "partial"]
    today_rent = [serialize_rent_charge(charge, session) for charge in charges if charge.due_date == today and charge.status not in {"paid", "paid_ahead"}]
    deferred = [
        serialize_rent_charge(charge, session)
        for charge in charges
        if charge.status == "deferred" and charge.deferral_until and charge.deferral_until <= today + timedelta(days=2)
    ]

    overdue_utilities = [serialize_bill_line(line, session) for line in utility_lines if line.status == "overdue"]
    partial_utilities = [serialize_bill_line(line, session) for line in utility_lines if line.status == "partial"]

    pending_expenses = session.scalars(
        select(Expense).where(Expense.source_funds == "personal", Expense.compensation_status != "compensated")
    ).all()

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
        if bill.status in {"issued", "draft"} and any(line.status != "paid" for line in bill.lines)
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
        "pending_personal_expenses": [serialize_expense(expense) for expense in pending_expenses],
        "stale_readings": stale_readings,
        "provider_debts": provider_debts,
        "suspicious_receipts": [serialize_payment_receipt(receipt, session) for receipt in suspicious_receipts],
    }


def money_text(value: float) -> str:
    return f"{money(value):,.2f}".replace(",", " ").replace(".", ",") + " ₽"


def first_open_rent_charge(lease: Lease) -> RentCharge | None:
    charges = sorted(lease.rent_charges, key=lambda item: item.due_date)
    for charge in charges:
        update_rent_charge_status(charge)
        if charge.status not in {"paid", "paid_ahead"}:
            return charge
    return None


def first_open_utility_line(session: Session, lease: Lease) -> UtilityBillLine | None:
    lines = session.scalars(select(UtilityBillLine).where(UtilityBillLine.lease_id == lease.id)).all()
    lines = sorted(lines, key=lambda item: (item.due_date or date.max, item.id))
    for line in lines:
        update_utility_line_status(line)
        if line.status not in {"paid", "paid_ahead"}:
            return line
    return None


def month_title(value: date | None) -> str:
    if not value:
        return ""
    return f"{MONTH_NAMES[value.month - 1]} {value.year}"


def outstanding_rent_charges(lease: Lease, today: date | None = None) -> list[RentCharge]:
    today = today or date.today()
    result: list[RentCharge] = []
    for charge in sorted(lease.rent_charges, key=lambda item: (item.due_date, item.id)):
        update_rent_charge_status(charge, today)
        debt = max(0.0, charge.ip_due - charge.ip_paid) + max(0.0, charge.personal_due - charge.personal_paid)
        if debt > 0.009 and charge.due_date <= today:
            result.append(charge)
    return result


def outstanding_utility_lines(session: Session, lease: Lease, today: date | None = None) -> list[UtilityBillLine]:
    today = today or date.today()
    result: list[UtilityBillLine] = []
    lines = session.scalars(select(UtilityBillLine).where(UtilityBillLine.lease_id == lease.id)).all()
    for line in sorted(lines, key=lambda item: (item.due_date or date.max, item.id)):
        update_utility_line_status(line, today)
        debt = max(0.0, line.total_amount - line.paid_amount)
        if debt > 0.009 and line.due_date and line.due_date <= today:
            result.append(line)
    return result


def build_message_context(
    session: Session,
    lease: Lease,
    charge: RentCharge | None = None,
    line: UtilityBillLine | None = None,
    custom_text: str = "",
) -> dict[str, str]:
    today = date.today()
    charge = charge or first_open_rent_charge(lease)
    line = line or first_open_utility_line(session, lease)
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

    current_utility_total = 0.0
    utility_due_date = ""
    utility_period = ""
    utility_service = ""
    if line:
        update_utility_line_status(line)
        current_utility_total = max(0.0, line.total_amount - line.paid_amount)
        utility_due_date = format_date(line.due_date) if line.due_date else ""
        utility_period = f"{format_date(line.bill.period_start)} - {format_date(line.bill.period_end)}"
        utility_service = line.bill.service.name

    rent_debts = outstanding_rent_charges(lease, today)
    utility_debts = outstanding_utility_lines(session, lease, today)
    total_rent_debt = sum(max(0.0, item.ip_due - item.ip_paid) + max(0.0, item.personal_due - item.personal_paid) for item in rent_debts)
    total_utility_debt = sum(max(0.0, item.total_amount - item.paid_amount) for item in utility_debts)

    rent_months = [month_title(item.due_date) for item in rent_debts]
    utility_items = [
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
        "utility_debt_details": "; ".join(utility_items),
        "all_debt_total": money_text(total_rent_debt + total_utility_debt),
        "custom_text": custom_text,
    }


def render_message_text(
    session: Session,
    template_key: str,
    lease: Lease,
    charge: RentCharge | None = None,
    line: UtilityBillLine | None = None,
    custom_text: str = "",
) -> str:
    if template_key == "custom":
        return custom_text.strip()
    context = build_message_context(session, lease, charge, line, custom_text)
    template = message_template(session, template_key)
    if template_key == "message_rent_overdue" and "{rent_debt_months" not in template:
        template = (
            template.rstrip()
            + "\nДолги по аренде: {rent_debt_months_count} мес. ({rent_debt_months})."
            + "\nПо месяцам: {rent_debt_months_details}."
            + "\nКоммуналка: {utility_debt_total}. Общий долг: {all_debt_total}."
        )
    return fill_template(template, context)


def serialize_message_target(session: Session, lease: Lease) -> dict[str, Any]:
    charge = first_open_rent_charge(lease)
    line = first_open_utility_line(session, lease)
    return {
        "lease_id": lease.id,
        "tenant_id": lease.tenant_id,
        "tenant": lease.tenant.full_name,
        "object": lease.apartment.object.name,
        "apartment": lease.apartment.name,
        "telegram": lease.tenant.telegram,
        "linked": tenant_chat_linked(session, lease),
        "rent_charge_id": charge.id if charge else None,
        "rent_status": charge.status if charge else "",
        "rent_debt": money(max(0.0, (charge.ip_due - charge.ip_paid) if charge else 0.0) + max(0.0, (charge.personal_due - charge.personal_paid) if charge else 0.0)),
        "rent_reminder": reminder_meta(session, lease, charge.due_date, rent_charge_id=charge.id) if charge else None,
        "utility_line_id": line.id if line else None,
        "utility_status": line.status if line else "",
        "utility_debt": money(max(0.0, (line.total_amount - line.paid_amount) if line else 0.0)),
        "utility_reminder": reminder_meta(session, lease, line.due_date, utility_line_id=line.id) if line else None,
    }


def send_tenant_message(
    session: Session,
    lease: Lease,
    template_key: str,
    charge: RentCharge | None = None,
    line: UtilityBillLine | None = None,
    custom_text: str = "",
    note: str = "manual",
) -> dict[str, Any]:
    chat_id = lease_chat_id(session, lease)
    if not chat_id:
        raise HTTPException(400, f"{lease.tenant.full_name} ещё не привязан к боту. Пусть сначала напишет /start.")
    text = render_message_text(session, template_key, lease, charge, line, custom_text)
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
        select(RentCharge).join(Lease).where(Lease.active.is_(True)).order_by(RentCharge.due_date, RentCharge.id)
    ).all()
    for charge in charges:
        update_rent_charge_status(charge, today)
        template_key = ""
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
        if reminder_sent_today(session, template_key, rent_charge_id=charge.id, today=today):
            summary["skipped_duplicate"] += 1
            continue
        try:
            send_tenant_message(session, charge.lease, template_key, charge=charge, note="auto")
            summary["sent"] += 1
        except HTTPException:
            summary["failed"] += 1

    lines = session.scalars(
        select(UtilityBillLine).join(Lease, UtilityBillLine.lease_id == Lease.id).where(Lease.active.is_(True)).order_by(UtilityBillLine.id)
    ).all()
    for line in lines:
        update_utility_line_status(line, today)
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
        if reminder_sent_today(session, "message_utility_bill", utility_line_id=line.id, today=today):
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
        "personal": "перевод",
        "unknown": "неизвестно",
    }.get(value, value or "неизвестно")


def receipt_status_label(value: str) -> str:
    return {
        "accepted": "принят",
        "suspicious": "нужна проверка",
    }.get(value, value or "неизвестно")


def receipt_storage_dir() -> Path:
    path = ROOT_DIR / "data" / "telegram_receipts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def telegram_media_descriptor(message: dict[str, Any]) -> tuple[str, str, str]:
    document = message.get("document")
    if document:
        file_id = document.get("file_id") or ""
        name = Path(document.get("file_name") or "telegram-document.bin").name
        mime = document.get("mime_type") or ""
        return file_id, name, mime
    photos = message.get("photo") or []
    if photos:
        largest = photos[-1]
        return largest.get("file_id") or "", f"telegram-photo-{largest.get('file_unique_id') or utc_now().timestamp()}.jpg", "image/jpeg"
    return "", "", ""


def save_telegram_media(session: Session, message: dict[str, Any]) -> Path | None:
    token = telegram_token(session)
    file_id, name, _mime = telegram_media_descriptor(message)
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


def apply_receipt_match(session: Session, lease: Lease, parsed: dict[str, Any]) -> tuple[str, str, int | None, list[str]]:
    amount = float(parsed.get("amount") or 0)
    channel = detect_receipt_channel(parsed)
    issues = receipt_validation_issues(parsed, get_settings(session), channel)
    if amount <= 0:
        issues.append("в чеке не удалось определить положительную сумму")

    if channel == "ip":
        plan, remaining = build_rent_plan(session, lease, "ip", amount, exact_only=False)
        if not plan or remaining > EPS:
            issues.append("не удалось честно разложить платёж по аренде")
        if issues:
            return "suspicious", "review", None, issues
        return "accepted", "rent", plan[0][0].id, []

    if channel == "personal":
        rent_plan, rent_remaining = build_rent_plan(session, lease, "personal", amount, exact_only=True)
        utility_plan, utility_remaining = build_utility_plan(session, lease, amount, exact_only=True)
        rent_exact = bool(rent_plan) and rent_remaining <= EPS
        utility_exact = bool(utility_plan) and utility_remaining <= EPS
        if issues:
            return "suspicious", "review", None, issues
        if rent_exact and not utility_exact:
            return "accepted", "rent", rent_plan[0][0].id, []
        if utility_exact and not rent_exact:
            return "accepted", "utility", utility_plan[0][0].id, []
        if rent_exact and utility_exact:
            return "suspicious", "review", None, ["сумма одинаково подходит и под аренду, и под коммуналку"]
        return "suspicious", "review", None, ["сумма не закрывает точный долг ни по аренде, ни по коммуналке"]

    return "suspicious", "review", None, ["тип перевода пока не удалось определить"]


def owner_receipt_alert_text(
    session: Session,
    lease: Lease | None,
    parsed: dict[str, Any] | None,
    receipt_status: str,
    issues: list[str],
    sender_label: str,
) -> str:
    summary_bits = [sender_label]
    if parsed:
        if parsed.get("recipient_name"):
            summary_bits.append(f"получатель: {parsed['recipient_name']}")
        if parsed.get("purpose"):
            summary_bits.append(f"назначение: {parsed['purpose']}")
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
    saved_path = save_telegram_media(session, message)
    if not saved_path:
        send_telegram_text(session, chat_id, message_template(session, "message_receipt_review"))
        return

    parsed: dict[str, Any] | None = None
    if saved_path.suffix.lower() == ".pdf":
        try:
            parsed = parse_receipt_file(saved_path)
        except Exception:
            parsed = None

    lease = receipt_candidate_lease(session, message, parsed, linked_lease)
    status = "suspicious"
    match_type = "review"
    linked_id: int | None = None
    issues = ["чек сохранён, но не удалось ничего распознать"]
    if parsed and lease:
        status, match_type, linked_id, issues = apply_receipt_match(session, lease, parsed)
    elif parsed and not lease:
        issues = ["чек разобран, но арендатор по нему пока не определён"]

    channel = detect_receipt_channel(parsed or {})
    paid_at = datetime.fromisoformat(parsed["paid_at"]) if parsed and parsed.get("paid_at") else utc_now()
    receipt_details = json.dumps(parsed or {}, ensure_ascii=False)
    stored_path = str(saved_path.relative_to(ROOT_DIR))
    if status == "accepted" and lease and parsed:
        try:
            if match_type == "rent":
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
                status="suspicious",
                recipient_name=(parsed or {}).get("recipient_name") or "",
                recipient_details=receipt_details,
                file_path=stored_path,
                notes="; ".join(issues),
            )
        )

    if status == "accepted":
        send_telegram_text(session, chat_id, message_template(session, "message_receipt_received"))
    else:
        send_telegram_text(session, chat_id, message_template(session, "message_receipt_review"))

    owner_id = telegram_owner_chat_id(session)
    if owner_id:
        send_telegram_text(
            session,
            owner_id,
            owner_receipt_alert_text(session, lease, parsed, status, issues, message_sender_label(message)),
            app_keyboard(app_base_url(session)),
        )
        if status != "accepted" and message.get("message_id"):
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


def telegram_help_text() -> str:
    return "\n".join(
        [
            "Команды бота:",
            "/start - приветствие",
            "/id - показать chat id",
            "/status - статус пульта",
            "/reports - открытые месячные отчёты",
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
                f"Привет. Я привязал этот чат к квартире {linked_lease.apartment.name}. Теперь сюда можно присылать чеки и я передам их владельцу.",
            )
            return
        send_telegram_text(
            session,
            chat_id,
            f"Привет. Текущий chat id: {chat_id}\nСохрани его в настройках приложения как Telegram owner chat id.",
            app_keyboard(base_url),
        )
        return

    if command == "/help":
        if is_owner:
            send_telegram_text(session, chat_id, telegram_help_text(), app_keyboard(base_url))
        else:
            send_telegram_text(session, chat_id, "Можно прислать чек PDF или фото. Я его сохраню и передам владельцу.")
        return

    if not owner_id:
        send_telegram_text(
            session,
            chat_id,
            f"Owner chat id ещё не настроен. Отправь /id и сохрани это значение в приложении. Сейчас chat id: {chat_id}",
        )
        return

    if not is_owner:
        if message.get("document") or message.get("photo"):
            handle_tenant_receipt_message(session, message, linked_lease)
            return
        send_telegram_text(
            session,
            chat_id,
            "Напиши /start или просто пришли чек. Остальное пока оставим без философии.",
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
        send_telegram_text(session, chat_id, "Открываю пульт.", app_keyboard(base_url))
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
    leases = session.scalars(select(Lease).where(Lease.active.is_(True)).order_by(Lease.start_date.desc())).all()
    return [serialize_message_target(session, lease) for lease in leases]


@app.post("/api/messages/send")
def send_message_to_tenant(payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    lease = session.get(Lease, int(payload["lease_id"]))
    if not lease or not lease.active:
        raise HTTPException(404, "Активный договор не найден")

    template_key = (payload.get("template_key") or "").strip()
    if template_key not in {"message_rent_due", "message_rent_overdue", "message_utility_bill", "custom"}:
        raise HTTPException(400, "Неизвестный шаблон сообщения")

    charge = session.get(RentCharge, int(payload["charge_id"])) if payload.get("charge_id") else None
    line = session.get(UtilityBillLine, int(payload["utility_line_id"])) if payload.get("utility_line_id") else None
    if charge and charge.lease_id != lease.id:
        raise HTTPException(400, "Этот арендный долг относится к другому жильцу")
    if line and line.lease_id != lease.id:
        raise HTTPException(400, "Этот коммунальный счёт относится к другому жильцу")
    result = send_tenant_message(
        session,
        lease,
        template_key,
        charge=charge,
        line=line,
        custom_text=(payload.get("custom_text") or "").strip(),
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
    )
    if not apartment.name:
        raise HTTPException(400, "Название квартиры обязательно")
    session.add(apartment)
    session.commit()
    session.refresh(apartment)
    return serialize_apartment(apartment)


@app.get("/api/tenants")
def list_tenants(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    return [serialize_tenant(tenant) for tenant in session.scalars(select(Tenant).order_by(Tenant.active.desc(), Tenant.full_name)).all()]


@app.get("/api/leases")
def list_leases(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    return [serialize_lease(lease) for lease in session.scalars(select(Lease).order_by(Lease.active.desc(), Lease.start_date.desc())).all()]


@app.post("/api/leases/onboard")
def onboard_tenant(payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    apartment_id = int(payload.get("apartment_id") or 0)
    if not apartment_id:
        raise HTTPException(400, "Выберите свободную квартиру")
    apartment = session.get(Apartment, apartment_id)
    if not apartment:
        raise HTTPException(404, "Квартира не найдена")
    active_lease = session.scalar(select(Lease).where(Lease.apartment_id == apartment.id, Lease.active.is_(True)))
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
    generate_rent_charges(session)
    session.commit()
    session.refresh(lease)
    return serialize_lease(lease)


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
        "lease": serialize_lease(lease),
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
        .where(RentCharge.due_date >= start_date, RentCharge.due_date <= end_date)
        .order_by(RentCharge.due_date, RentCharge.id)
    ).all()
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
    if channel not in {"ip", "personal"}:
        raise HTTPException(400, "канал платежа должен быть ip или personal")
    amount = float(payload.get("amount") or 0)
    if amount <= 0:
        raise HTTPException(400, "сумма платежа должна быть больше нуля")

    create_rent_receipts(
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
    )
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
        "receipts": [serialize_payment_receipt(receipt, session) for receipt in receipts],
    }


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
        if channel not in {"ip", "personal"}:
            raise HTTPException(400, "для аренды нужен канал ip или personal")
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
        )
    elif kind == "utility":
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
    if payload.get("channel") and receipt.rent_charge_id:
        channel = payload.get("channel")
        if channel not in {"ip", "personal"}:
            raise HTTPException(400, "для аренды канал платежа должен быть ip или personal")
        receipt.channel = channel
    if receipt.status == "accepted" and old_lease_id:
        session.flush()
        recalculate_lease_balances(session, old_lease_id)
    session.commit()
    return serialize_payment_receipt(receipt, session)


@app.delete("/api/payment-receipts/{receipt_id}")
def delete_payment_receipt(receipt_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    receipt = session.get(PaymentReceipt, receipt_id)
    if not receipt:
        raise HTTPException(404, "платёж не найден")
    lease_id = receipt.lease_id
    status = receipt.status
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
    channel = channel_override if channel_override in {"ip", "personal"} else receipt.channel if receipt.channel in {"ip", "personal"} else detect_receipt_channel(parsed)
    lease = session.get(Lease, receipt.lease_id) if receipt.lease_id else None
    if action in {"accept_rent", "accept_utility"} and not lease:
        raise HTTPException(400, "нужна аренда для этой операции")

    moderation_note = f"модерация owner{': ' + note if note else ''}"
    if action == "accept_rent":
        create_rent_receipts(
            session,
            lease,
            "ip" if channel == "ip" else "personal",
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


@app.post("/api/utility-bills/calculate")
def create_utility_bill(payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
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


@app.post("/api/utility-bills/{bill_id}/issue")
def issue_utility_bill(bill_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    bill = session.get(UtilityBill, bill_id)
    if not bill:
        raise HTTPException(404, "Коммунальный счёт не найден")
    due = resident_due_date(bill.service)
    bill.status = "issued"
    bill.due_date = due
    for line in bill.lines:
        line.status = "issued"
        line.issued_at = utc_now()
        line.due_date = due
    session.commit()
    session.refresh(bill)
    return serialize_bill(bill)


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
        ["Дата", "Период", "Объект", "Квартира", "Жилец", "ИП начислено", "ИП оплачено", "Личный начислено", "Личный оплачено", "Долг", "Статус"],
    )
    for charge in charges:
        data = serialize_rent_charge(charge)
        ws.append(
            [
                data["due_date"],
                f'{data["period_start"]} - {data["period_end"]}',
                data["object"],
                data["apartment"],
                data["tenant"],
                data["ip_due"],
                data["ip_paid"],
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
    wb = Workbook()
    ws = setup_sheet(wb, "Сводка", ["Показатель", "Значение"])
    ws.append(["Период", f"{start_date} - {end_date}"])
    ws.append(["Статус", summary["label"]])
    ws.append(["Всего проблем", summary["issue_count"]])
    ws.append(["Критических", summary["danger_count"]])
    ws.append(["Предупреждений", summary["warning_count"]])

    issues_ws = setup_sheet(wb, "Проблемы", ["Важность", "Проблема", "Количество", "Комментарий"])
    for issue in summary["issues"]:
        issues_ws.append([issue["severity"], issue["title"], issue["count"], issue["detail"]])

    rent_ws = setup_sheet(
        wb,
        "Аренда",
        ["Дата", "Период", "Объект", "Квартира", "Жилец", "Начислено", "Оплачено", "Долг", "Статус"],
    )
    rent_charges = session.scalars(
        select(RentCharge).where(RentCharge.due_date >= start_date, RentCharge.due_date <= end_date).order_by(RentCharge.due_date)
    ).all()
    for charge in rent_charges:
        data = serialize_rent_charge(charge)
        rent_ws.append(
            [
                data["due_date"],
                f'{data["period_start"]} - {data["period_end"]}',
                data["object"],
                data["apartment"],
                data["tenant"],
                data["total_due"],
                data["total_paid"],
                data["debt"],
                report_status_label(data["status"], charge.due_date),
            ]
        )

    utilities_ws = setup_sheet(
        wb,
        "Коммуналка",
        ["Период", "Объект", "Услуга", "Квартира", "Жилец", "Сумма", "Оплачено", "Долг", "Статус", "Поставщик оплачен"],
    )
    bills = session.scalars(
        select(UtilityBill)
        .where(UtilityBill.period_start >= start_date, UtilityBill.period_start <= end_date)
        .order_by(UtilityBill.period_start)
    ).all()
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
