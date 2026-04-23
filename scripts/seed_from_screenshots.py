from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from rental_manager.database import SessionLocal, init_db
from rental_manager.models import (
    Apartment,
    Expense,
    Lease,
    Meter,
    MeterReading,
    PaymentReceipt,
    RentalObject,
    RentCharge,
    Tariff,
    Tenant,
    UtilityBill,
    UtilityService,
)
from rental_manager.services.billing import generate_rent_charges, update_rent_charge_status
from rental_manager.services.seed import seed_if_empty
from seed_demo import clear_demo_data


IMPORT_MARK = "SCREENSHOT_IMPORT"

APARTMENT_ALIASES = {
    "Белый дом 1": "БД1",
    "Белый дом 2": "БД2",
    "Белый дом 3": "БД3",
    "Белый дом 4": "БД4",
    "Чёрный дом 1": "ЧД1",
    "Черный дом 1": "ЧД1",
    "Чёрный дом 2": "ЧД2",
    "Черный дом 2": "ЧД2",
    "Чёрный дом 3": "ЧД3",
    "Черный дом 3": "ЧД3",
    "Чёрный дом 4": "ЧД4",
    "Черный дом 4": "ЧД4",
    "Баня 1": "Баня 1",
    "Баня 2": "Баня 2",
    "Баня 3": "Баня 3",
    "Баня 4": "Баня 4",
}

OBJECT_ALIASES = {
    "Белый дом": "Белый дом",
    "Чёрный дом": "Чёрный дом",
    "Черный дом": "Чёрный дом",
    "Баня": "Баня",
    "Бан": "Баня",
}

SERVICE_KINDS = {
    "Электричество": "electricity",
    "Вода": "water",
    "Газ": "gas",
}


TENANTS = [
    {"name": "Мишаня", "phone": "", "apartment": "Баня 2", "rent": 20000, "payment_day": 5, "start": "2025-09-05", "active": True},
    {"name": "Никита Холобаев", "phone": "89134628761", "apartment": "Баня 3", "rent": 20000, "payment_day": 6, "start": "2026-06-06", "active": True},
    {"name": "Денис", "phone": "", "apartment": "Белый дом 3", "rent": 20000, "payment_day": 2, "start": "2025-10-02", "active": True},
    {"name": "Виталий", "phone": "", "apartment": "Белый дом 2", "rent": 20000, "payment_day": 2, "start": "2024-07-02", "active": True},
    {"name": "Евгений", "phone": "", "apartment": "Чёрный дом 2", "rent": 20000, "payment_day": 14, "start": "2021-06-14", "active": True},
    {"name": "Сергей", "phone": "", "apartment": "Чёрный дом 4", "rent": 20000, "payment_day": 5, "start": "2021-04-05", "active": True},
    {"name": "Сёма", "phone": "", "apartment": "Чёрный дом 3", "rent": 20000, "payment_day": 29, "start": "2018-09-29", "active": True},
    {"name": "Максим", "phone": "89134633699", "apartment": "Чёрный дом 1", "rent": 20000, "payment_day": 7, "start": "2025-08-07", "end": "2026-03-03", "active": False},
    {"name": "Ольга", "phone": "89139873869", "apartment": "Белый дом 4", "rent": 20000, "payment_day": 21, "start": "2025-10-21", "active": True},
    {"name": "Массажка", "phone": "", "apartment": "Баня 4", "rent": 20000, "payment_day": 17, "start": "2022-12-17", "active": True, "notes": "В старой базе есть обрезанная заметка: Юля погибла 28.01, аренд..."},
    {"name": "Гусева Татьяна Константиновна", "phone": "89046841820", "apartment": "Чёрный дом 1", "rent": 20000, "payment_day": 4, "start": "2026-03-04", "active": True},
]

RENT_PAYMENTS = [
    ("2026-01-08", 20000, "Баня 2", "Мишаня", "ИП"),
    ("2026-02-06", 20000, "Баня 2", "Мишаня", "ИП"),
    ("2026-01-13", 20000, "Белый дом 2", "Виталий", "ИП"),
    ("2026-01-03", 20000, "Белый дом 3", "Денис", "ИП"),
    ("2026-01-21", 20000, "Белый дом 4", "Ольга", "ИП"),
    ("2026-01-13", 20000, "Чёрный дом 1", "Максим", "ИП"),
    ("2026-01-16", 20000, "Чёрный дом 2", "Евгений", "ИП"),
    ("2026-01-17", 20000, "Чёрный дом 4", "Сергей", "ИП"),
    ("2026-02-01", 20000, "Белый дом 3", "Денис", "ИП"),
    ("2026-01-21", 20000, "Баня 4", "Массажка", "Банковский перевод"),
    ("2026-02-09", 20000, "Чёрный дом 3", "Сёма", "ИП"),
    ("2026-02-09", 10000, "Чёрный дом 3", "Сёма", "ИП"),
    ("2026-02-06", 20000, "Баня 4", "Массажка", "ИП"),
    ("2026-02-13", 20000, "Чёрный дом 2", "Евгений", "ИП"),
    ("2025-12-15", 20000, "Баня 4", "Массажка", "Банковский перевод"),
    ("2026-02-24", 20000, "Чёрный дом 1", "Гусева Татьяна Константиновна", "Банковский перевод"),
    ("2026-03-01", 20000, "Белый дом 3", "Денис", "ИП"),
    ("2026-03-04", 20000, "Чёрный дом 1", "Гусева Татьяна Константиновна", "Наличные"),
]

UTILITY_PAYMENTS = [
    ("2025-10-02", 1900, "Чёрный дом 1", "Максим", "Банковский перевод"),
    ("2025-11-19", 6160, "Чёрный дом 1", "Максим", "Банковский перевод"),
    ("2026-01-21", 14663, "Чёрный дом - общий", "", "Онлайн"),
    ("2026-01-21", 16840, "Белый дом - общий", "", "Онлайн"),
    ("2025-12-15", 10318, "Белый дом - общий", "", "Онлайн"),
    ("2025-11-13", 18688, "Чёрный дом - общий", "", "Онлайн"),
    ("2025-09-29", 4944, "Белый дом - общий", "", "Онлайн"),
    ("2025-12-24", 10080, "Баня 4", "Массажка", "Банковский перевод"),
    ("2025-12-30", 5000, "Белый дом 2", "Виталий", "Банковский перевод"),
    ("2026-02-17", 2321, "Чёрный дом 4", "Сергей", "Банковский перевод"),
    ("2025-11-15", 3600, "Белый дом 4", "Ольга", "Банковский перевод"),
    ("2025-12-24", 3399, "Белый дом 4", "Ольга", "Банковский перевод"),
    ("2026-02-17", 7481, "Белый дом 4", "Ольга", "Банковский перевод"),
    ("2026-02-18", 16506, "Белый дом - общий", "", "Онлайн"),
    ("2026-02-18", 15334, "Чёрный дом - общий", "", "Онлайн"),
    ("2026-02-19", 5124, "Баня 2", "Мишаня", "Банковский перевод"),
    ("2026-02-20", 6000, "Белый дом 3", "Денис", "Банковский перевод"),
    ("2025-11-22", 5860, "Белый дом 3", "Денис", "Банковский перевод"),
    ("2026-02-24", 2200, "Чёрный дом 1", "Максим", "Банковский перевод"),
    ("2026-03-13", 2620, "Белый дом 3", "Денис", "Банковский перевод"),
    ("2026-03-23", 7394, "Чёрный дом - общий", "", "Онлайн"),
    ("2026-03-23", 45887, "Белый дом - общий", "", "Онлайн"),
]

METER_READINGS = [
    ("Чёрный дом - общий", "Электричество", 237020, "2026-01-20", "На скрине похоже на 23702; нормализовано до 237020 по соседним значениям."),
    ("Чёрный дом 1", "Электричество", 7077, "2025-09-25", ""),
    ("Чёрный дом 1", "Электричество", 7275, "2026-01-20", ""),
    ("Чёрный дом - общий", "Электричество", 227960, "2025-09-25", ""),
    ("Чёрный дом 2", "Электричество", 21206, "2026-01-20", ""),
    ("Чёрный дом 3", "Электричество", 18047, "2026-01-20", ""),
    ("Чёрный дом 4", "Электричество", 9297, "2026-01-20", ""),
    ("Белый дом - общий", "Электричество", 53763, "2026-01-20", ""),
    ("Белый дом 1", "Электричество", 1426, "2026-01-20", ""),
    ("Белый дом 2", "Электричество", 2793, "2026-01-20", ""),
    ("Белый дом 3", "Электричество", 2847, "2026-01-20", ""),
    ("Белый дом 4", "Электричество", 2722, "2026-01-20", ""),
    ("Баня - общий", "Газ", 4710, "2026-01-26", ""),
    ("Баня - общий", "Вода", 4571, "2026-01-26", ""),
    ("Баня - общий", "Электричество", 42690, "2026-01-26", ""),
    ("Чёрный дом 1", "Электричество", 7245, "2025-12-30", ""),
    ("Чёрный дом 2", "Электричество", 20810, "2025-12-30", ""),
    ("Чёрный дом 3", "Электричество", 17765, "2025-12-30", ""),
    ("Чёрный дом 4", "Электричество", 9081, "2025-12-30", ""),
    ("Чёрный дом - общий", "Электричество", 235632, "2025-12-30", ""),
    ("Баня - общий", "Электричество", 40922, "2025-12-24", ""),
    ("Баня - общий", "Вода", 4528, "2025-12-24", ""),
    ("Баня - общий", "Газ", 4577, "2025-12-24", ""),
    ("Чёрный дом 2", "Электричество", 18315, "2025-09-25", ""),
    ("Чёрный дом 3", "Электричество", 16618, "2025-09-25", ""),
    ("Чёрный дом 4", "Электричество", 8328, "2025-09-25", ""),
    ("Белый дом - общий", "Электричество", 35166, "2025-09-25", ""),
    ("Белый дом 1", "Электричество", 1368, "2025-09-25", ""),
    ("Белый дом 4", "Электричество", 2513, "2025-09-25", ""),
    ("Белый дом 3", "Электричество", 2550, "2025-09-25", ""),
    ("Белый дом 2", "Электричество", 2533, "2025-09-25", ""),
    ("Белый дом - общий", "Электричество", 40892, "2025-11-11", ""),
    ("Белый дом - общий", "Электричество", 69018, "2026-04-19", ""),
    ("Чёрный дом - общий", "Электричество", 242877, "2026-04-19", ""),
    ("Белый дом 1", "Электричество", 1472, "2026-04-19", ""),
    ("Белый дом 2", "Электричество", 3003, "2026-04-19", ""),
    ("Белый дом 3", "Электричество", 3031, "2026-04-19", ""),
    ("Белый дом 4", "Электричество", 2921, "2026-04-19", ""),
    ("Чёрный дом 1", "Электричество", 7504, "2026-04-19", ""),
    ("Чёрный дом 2", "Электричество", 24540, "2026-04-19", ""),
    ("Чёрный дом 3", "Электричество", 19161, "2026-04-19", ""),
    ("Чёрный дом 4", "Электричество", 9796, "2026-04-19", ""),
]

TARIFFS = [
    ("Чёрный дом", "electricity", "Чёрный дом - Электричество", 4.12, "2025-01-01"),
    ("Чёрный дом", "electricity", "Чёрный дом - Электричество актуальный", 4.18, "2026-04-23"),
    ("Белый дом", "electricity", "Белый дом - Электричество", 4.12, "2025-01-01"),
    ("Баня", "water", "Баня - Вода 2026", 28.60, "2025-01-01"),
    ("Баня", "gas", "Баня - Газ 2025", 7.98, "2025-01-01"),
    ("Баня", "electricity", "Баня - Электричество 2026", 7.40, "2025-10-01"),
]

EXPENSES = [
    ("2026-03-05", 2700, "Смеситель для душа ЧД 1", "Безнал", "personal", "Чёрный дом 1", True),
    ("2026-03-09", 1500, "Озон, смеситель повторно", "Безнал", "rent_budget", "Чёрный дом 1", True),
    ("2026-03-09", 1250, "4шт лопаты баня, насос", "Безнал", "rent_budget", "Чёрный дом 1", True),
    ("2026-03-09", 500, "Работа Фёдора, смеситель", "Безнал", "rent_budget", "Чёрный дом 1", True),
    ("2026-03-10", 1752, "Пена, баня", "Безнал", "rent_budget", "Чёрный дом 1", True),
    ("2026-03-23", 5000, "Баня электрика", "Безнал", "rent_budget", "Чёрный дом 1", True),
    ("2026-03-24", 2500, "Откачка", "Безнал", "rent_budget", "Чёрный дом 1", True),
    ("2025-10-29", 2200, "Откачка", "Безнал", "personal", "", False),
    ("2025-10-09", 1700, "Откачка баня", "Безнал", "personal", "", False),
    ("2025-10-02", 2200, "Откачка баня", "Безнал", "personal", "", False),
    ("2025-08-29", 3000, "Откачка баня", "Безнал", "personal", "", False),
    ("2025-08-27", 2200, "Откачка баня", "Безнал", "personal", "", False),
    ("2025-08-09", 2200, "Откачка баня", "Безнал", "personal", "", False),
    ("2025-07-22", 1700, "Откачка баня", "Безнал", "personal", "", False),
    ("2025-07-16", 2200, "Откачка баня", "Безнал", "personal", "", False),
    ("2025-07-10", 2200, "Откачка баня", "Безнал", "personal", "", False),
    ("2025-06-30", 1700, "Откачка баня", "Безнал", "personal", "", False),
    ("2025-12-07", 371, "ЧД расширительный бак", "Безнал", "personal", "", False),
    ("2025-10-22", 4543, "Фанерный дом электрика", "Безнал", "personal", "", False),
    ("2025-10-20", 4337, "ЧД котёл", "Безнал", "personal", "", False),
    ("2025-10-14", 2150, "ЧД котёл", "Безнал", "personal", "", False),
    ("2025-09-08", 1500, "БД унитаз арматура", "Безнал", "personal", "", False),
    ("2025-07-24", 1300, "ЧД бурение защитные средства", "Безнал", "personal", "", False),
    ("2025-07-24", 1552, "ЧД бурение диск и ключ", "Безнал", "personal", "", False),
    ("2025-07-15", 3825, "Баня краска и сантехника", "Безнал", "personal", "", False),
    ("2025-07-03", 9683, "БД постель, смеситель, лейки", "Безнал", "personal", "", False),
    ("2025-05-01", 671, "ЧД1 слив душ", "Безнал", "personal", "Чёрный дом 1", False),
    ("2025-06-09", 872, "В27 сифон для мойки", "Безнал", "personal", "", False),
    ("2025-04-05", 2437, "ЧД1 люстра и лампочки", "Безнал", "personal", "Чёрный дом 1", False),
    ("2026-03-24", 500, "Фёдор баня работа счётчик", "Безнал", "personal", "", False),
    ("2026-04-15", 10000, "Оплата техобслуживания", "Безнал", "rent_budget", "Чёрный дом 1", True),
    ("2026-04-15", 350, "Уборка ЧД1", "Безнал", "rent_budget", "Чёрный дом 1", True),
    ("2026-02-17", 1800, "ЧД1 объявление", "Безнал", "rent_budget", "Чёрный дом 1", True),
    ("2026-03-29", 1700, "ЧД1 продвижение", "Безнал", "rent_budget", "Чёрный дом 1", True),
    ("2026-04-19", 1836, "ЧД1 объявление", "Безнал", "rent_budget", "Чёрный дом 1", True),
    ("2026-04-16", 2700, "Откачка баня", "Наличные", "personal", "", False),
]


def main() -> None:
    init_db()
    with SessionLocal() as session:
        seed_if_empty(session)
        clear_demo_data(session)
        clear_screenshot_data(session)
        seed_tariffs(session)
        leases = seed_leases(session)
        generate_rent_charges(session, until=date(2026, 5, 31))
        mark_old_rent_paid(session)
        seed_rent_payments(session, leases)
        seed_utility_payments(session, leases)
        seed_meter_readings(session)
        seed_expenses(session)
        session.commit()
        print_summary(session)


def parse_iso(value: str) -> date:
    return date.fromisoformat(value)


def clear_screenshot_data(session: Session) -> None:
    tenants = session.scalars(select(Tenant).where(Tenant.notes.like(f"%{IMPORT_MARK}%"))).all()
    leases = [lease for tenant in tenants for lease in tenant.leases]
    lease_ids = [lease.id for lease in leases if lease.id is not None]
    receipt_filters = [PaymentReceipt.notes.like(f"%{IMPORT_MARK}%")]
    if lease_ids:
        receipt_filters.append(PaymentReceipt.lease_id.in_(lease_ids))
    for receipt in session.scalars(select(PaymentReceipt).where(or_(*receipt_filters))).all():
        session.delete(receipt)
    for expense in session.scalars(select(Expense).where(Expense.notes.like(f"%{IMPORT_MARK}%"))).all():
        session.delete(expense)
    for bill in session.scalars(select(UtilityBill).where(UtilityBill.notes.like(f"%{IMPORT_MARK}%"))).all():
        session.delete(bill)
    if lease_ids:
        for charge in session.scalars(select(RentCharge).where(RentCharge.lease_id.in_(lease_ids))).all():
            session.delete(charge)
    for reading in session.scalars(select(MeterReading).where(MeterReading.note.like(f"%{IMPORT_MARK}%"))).all():
        session.delete(reading)
    for lease in leases:
        session.delete(lease)
    for tenant in tenants:
        session.delete(tenant)
    session.flush()


def object_by_name(session: Session, name: str) -> RentalObject:
    normalized = OBJECT_ALIASES.get(name, name)
    obj = session.scalar(select(RentalObject).where(RentalObject.name == normalized))
    if not obj:
        raise RuntimeError(f"Не найден объект {name}")
    return obj


def apartment_by_label(session: Session, label: str) -> Apartment | None:
    if not label:
        return None
    name = APARTMENT_ALIASES.get(label, label)
    return session.scalar(select(Apartment).where(Apartment.name == name).limit(1))


def service_for(session: Session, object_name: str, kind: str) -> UtilityService:
    obj = object_by_name(session, object_name)
    service = session.scalar(
        select(UtilityService)
        .where(UtilityService.object_id == obj.id, UtilityService.kind == kind)
        .limit(1)
    )
    if not service:
        raise RuntimeError(f"Не найдена услуга {kind} для {object_name}")
    return service


def seed_tariffs(session: Session) -> None:
    for tariff in session.scalars(select(Tariff)).all():
        session.delete(tariff)
    session.flush()
    for object_name, kind, name, price, starts_on in TARIFFS:
        service = service_for(session, object_name, kind)
        session.add(
            Tariff(
                service_id=service.id,
                starts_on=parse_iso(starts_on),
                name=f"{name} ({IMPORT_MARK})",
                tiers_json=json.dumps([{"limit": None, "price": price}], ensure_ascii=False),
            )
        )
    session.flush()


def seed_leases(session: Session) -> dict[str, Lease]:
    leases: dict[str, Lease] = {}
    for row in TENANTS:
        apartment = apartment_by_label(session, row["apartment"])
        if not apartment:
            raise RuntimeError(f"Не найдена квартира {row['apartment']}")
        tenant = Tenant(
            full_name=row["name"],
            phone=row.get("phone", ""),
            whatsapp=row.get("phone", ""),
            notes=f"{IMPORT_MARK}: tenant from screenshot",
            active=bool(row.get("active", True)),
        )
        session.add(tenant)
        session.flush()
        lease = Lease(
            apartment_id=apartment.id,
            tenant_id=tenant.id,
            start_date=parse_iso(row["start"]),
            end_date=parse_iso(row["end"]) if row.get("end") else None,
            payment_day=int(row["payment_day"]),
            ip_amount=float(row["rent"]),
            personal_amount=0,
            notes=f"{IMPORT_MARK}: {row.get('notes', '')}".strip(),
            active=bool(row.get("active", True)),
        )
        session.add(lease)
        leases[f"{row['apartment']}::{row['name']}"] = lease
    session.flush()
    return leases


def mark_old_rent_paid(session: Session) -> None:
    cutoff = date(2026, 1, 1)
    for charge in session.scalars(select(RentCharge).where(RentCharge.due_date < cutoff)).all():
        charge.ip_paid = charge.ip_due
        charge.personal_paid = charge.personal_due
        update_rent_charge_status(charge, today=cutoff)
    session.flush()


def find_lease(leases: dict[str, Lease], apartment: str, tenant_name: str) -> Lease | None:
    if not tenant_name:
        return None
    exact = leases.get(f"{apartment}::{tenant_name}")
    if exact:
        return exact
    tenant_lower = tenant_name.lower()
    for key, lease in leases.items():
        key_apartment, key_tenant = key.split("::", 1)
        if key_apartment == apartment and (tenant_lower in key_tenant.lower() or key_tenant.lower() in tenant_lower):
            return lease
    return None


def seed_rent_payments(session: Session, leases: dict[str, Lease]) -> None:
    for paid_on, amount, apartment, tenant_name, method in RENT_PAYMENTS:
        paid_date = parse_iso(paid_on)
        lease = find_lease(leases, apartment, tenant_name)
        charge = apply_rent_payment(session, lease, paid_date, amount) if lease else None
        session.add(
            PaymentReceipt(
                lease_id=lease.id if lease else None,
                rent_charge_id=charge.id if charge else None,
                apartment_id=lease.apartment_id if lease else apartment_by_label(session, apartment).id if apartment_by_label(session, apartment) else None,
                amount=float(amount),
                channel="ip" if method == "ИП" else "personal",
                paid_at=datetime.combine(paid_date, datetime.min.time()),
                source="screenshot",
                status="accepted",
                recipient_name=method,
                notes=f"{IMPORT_MARK}: rent payment from old base",
            )
        )
    session.flush()


def apply_rent_payment(session: Session, lease: Lease | None, paid_date: date, amount: float) -> RentCharge | None:
    if not lease:
        return None
    charges = sorted(
        [charge for charge in lease.rent_charges if charge.due_date <= paid_date],
        key=lambda item: item.due_date,
    )
    if not charges:
        return None
    charge = next((item for item in charges if item.ip_paid + item.personal_paid + 0.009 < item.ip_due + item.personal_due), charges[-1])
    remaining = float(amount)
    ip_left = max(0, charge.ip_due - charge.ip_paid)
    ip_payment = min(ip_left, remaining)
    charge.ip_paid = round(charge.ip_paid + ip_payment, 2)
    remaining -= ip_payment
    personal_left = max(0, charge.personal_due - charge.personal_paid)
    personal_payment = min(personal_left, remaining)
    charge.personal_paid = round(charge.personal_paid + personal_payment, 2)
    remaining -= personal_payment
    if remaining > 0:
        charge.ip_paid = round(charge.ip_paid + remaining, 2)
    update_rent_charge_status(charge, today=paid_date)
    session.flush()
    return charge


def seed_utility_payments(session: Session, leases: dict[str, Lease]) -> None:
    for paid_on, amount, apartment, tenant_name, method in UTILITY_PAYMENTS:
        paid_date = parse_iso(paid_on)
        lease = find_lease(leases, apartment, tenant_name)
        apartment_obj = apartment_by_label(session, apartment)
        session.add(
            PaymentReceipt(
                lease_id=lease.id if lease else None,
                apartment_id=apartment_obj.id if apartment_obj else None,
                amount=float(amount),
                channel="utility",
                paid_at=datetime.combine(paid_date, datetime.min.time()),
                source="screenshot",
                status="accepted",
                recipient_name=method,
                notes=f"{IMPORT_MARK}: utility payment, old apartment field={apartment}",
            )
        )
    session.flush()


def meter_for_label(session: Session, label: str, service_type: str) -> Meter:
    kind = SERVICE_KINDS[service_type]
    if "- общий" in label:
        object_name = label.split("-", 1)[0].strip()
        service = service_for(session, object_name, kind)
        meter = session.scalar(
            select(Meter)
            .where(Meter.service_id == service.id, Meter.scope == "object")
            .limit(1)
        )
    else:
        apartment = apartment_by_label(session, label)
        if not apartment:
            raise RuntimeError(f"Не найдена квартира для счётчика {label}")
        service = service_for(session, apartment.object.name, kind)
        meter = session.scalar(
            select(Meter)
            .where(Meter.service_id == service.id, Meter.apartment_id == apartment.id)
            .limit(1)
        )
    if not meter:
        raise RuntimeError(f"Не найден счётчик {label} / {service_type}")
    return meter


def seed_meter_readings(session: Session) -> None:
    for label, service_type, value, reading_on, note in METER_READINGS:
        meter = meter_for_label(session, label, service_type)
        reading_date = parse_iso(reading_on)
        existing = session.scalar(select(MeterReading).where(MeterReading.meter_id == meter.id, MeterReading.reading_date == reading_date))
        text_note = f"{IMPORT_MARK}: {note}".strip()
        if existing:
            existing.value = float(value)
            existing.note = text_note
        else:
            session.add(MeterReading(meter_id=meter.id, reading_date=reading_date, value=float(value), note=text_note))
    session.flush()


def seed_expenses(session: Session) -> None:
    for expense_on, amount, description, method, source, apartment_label, compensated in EXPENSES:
        apartment = apartment_by_label(session, apartment_label)
        obj = apartment.object if apartment else None
        status = "not_required" if source != "personal" else "compensated" if compensated else "pending"
        expense = Expense(
            expense_date=parse_iso(expense_on),
            object_id=obj.id if obj else None,
            apartment_id=apartment.id if apartment else None,
            category="расход со скрина",
            amount=float(amount),
            source_funds=source,
            payment_method=method,
            description=description,
            compensation_status=status,
            compensated_at=datetime.combine(parse_iso(expense_on), datetime.min.time()) if status == "compensated" else None,
            notes=f"{IMPORT_MARK}: expense from old base",
        )
        session.add(expense)
    session.flush()


def print_summary(session: Session) -> None:
    tenants = session.scalar(select(func.count(Tenant.id)).where(Tenant.notes.like(f"%{IMPORT_MARK}%")))
    leases = session.scalar(select(func.count(Lease.id)).join(Tenant).where(Tenant.notes.like(f"%{IMPORT_MARK}%")))
    readings = session.scalar(select(func.count(MeterReading.id)).where(MeterReading.note.like(f"%{IMPORT_MARK}%")))
    expenses = session.scalar(select(func.count(Expense.id)).where(Expense.notes.like(f"%{IMPORT_MARK}%")))
    receipts = session.scalar(select(func.count(PaymentReceipt.id)).where(PaymentReceipt.notes.like(f"%{IMPORT_MARK}%")))
    print(f"Imported tenants={tenants}, leases={leases}, readings={readings}, expenses={expenses}, receipts={receipts}")


if __name__ == "__main__":
    main()
