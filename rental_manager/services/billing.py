from __future__ import annotations

import json
import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from rental_manager.models import (
    Apartment,
    Lease,
    Meter,
    MeterReading,
    RentCharge,
    Tariff,
    UtilityBill,
    UtilityBillLine,
    UtilityService,
)

LEGACY_IMPORT_MARK = "SCREENSHOT_IMPORT"
LEGACY_PERSONAL_IGNORE_BEFORE = date(2026, 4, 1)


def parse_date(value: str | date | None, default: date | None = None) -> date:
    if value is None or value == "":
        if default is None:
            raise ValueError("date is required")
        return default
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def money(value: float) -> float:
    return round(float(value or 0), 2)


def status_for_amount(due: float, paid: float) -> str:
    due = float(due or 0)
    paid = float(paid or 0)
    if paid <= 0:
        return "pending"
    if paid + 0.009 < due:
        return "partial"
    if paid > due + 0.009:
        return "paid_ahead"
    return "paid"


def update_rent_charge_status(charge: RentCharge, today: date | None = None) -> str:
    today = today or date.today()
    apply_legacy_personal_relief(charge)
    ip_status = status_for_amount(charge.ip_due, charge.ip_paid)
    personal_status = status_for_amount(charge.personal_due, charge.personal_paid)
    ip_paid = float(charge.ip_paid or 0)
    personal_paid = float(charge.personal_paid or 0)

    if ip_status in {"paid", "paid_ahead"} and personal_status in {"paid", "paid_ahead"}:
        charge.status = "paid_ahead" if "paid_ahead" in {ip_status, personal_status} else "paid"
    elif charge.deferral_until and charge.deferral_until >= today:
        charge.status = "deferred"
    elif ip_paid > 0 or personal_paid > 0:
        charge.status = "partial"
    elif charge.due_date < today:
        charge.status = "overdue"
    else:
        charge.status = "pending"
    return charge.status


def is_legacy_imported_charge(charge: RentCharge) -> bool:
    lease = getattr(charge, "lease", None)
    if not lease:
        return False
    lease_notes = (lease.notes or "")
    tenant_notes = (lease.tenant.notes or "") if lease.tenant else ""
    return LEGACY_IMPORT_MARK in lease_notes or LEGACY_IMPORT_MARK in tenant_notes


def apply_legacy_personal_relief(charge: RentCharge) -> None:
    if charge.due_date >= LEGACY_PERSONAL_IGNORE_BEFORE:
        return
    if not is_legacy_imported_charge(charge):
        return
    if float(charge.ip_paid or 0) <= 0:
        return
    if float(charge.personal_due or 0) <= float(charge.personal_paid or 0):
        return
    charge.personal_paid = float(charge.personal_due or 0)


def effective_due_date(year: int, month: int, payment_day: int) -> date:
    _, last_day = calendar.monthrange(year, month)
    if payment_day <= last_day:
        return date(year, month, payment_day)
    if month == 12:
        return date(year + 1, 1, 1)
    return date(year, month + 1, 1)


def add_month(year: int, month: int) -> tuple[int, int]:
    if month == 12:
        return year + 1, 1
    return year, month + 1


def next_due_after(current_due: date, payment_day: int) -> date:
    year, month = current_due.year, current_due.month
    while True:
        candidate = effective_due_date(year, month, payment_day)
        if candidate > current_due:
            return candidate
        year, month = add_month(year, month)


def generate_rent_charges(session: Session, until: date | None = None) -> int:
    until = until or (date.today() + timedelta(days=70))
    created = 0
    leases = session.scalars(select(Lease).where(Lease.active.is_(True))).all()
    for lease in leases:
        due = effective_due_date(lease.start_date.year, lease.start_date.month, lease.payment_day)
        if due < lease.start_date:
            due = next_due_after(due, lease.payment_day)
        while due <= until and (lease.end_date is None or due <= lease.end_date):
            exists = session.scalar(
                select(RentCharge).where(
                    RentCharge.lease_id == lease.id,
                    RentCharge.due_date == due,
                )
            )
            next_due = next_due_after(due, lease.payment_day)
            if not exists:
                session.add(
                    RentCharge(
                        lease_id=lease.id,
                        period_start=due,
                        period_end=next_due - timedelta(days=1),
                        due_date=due,
                        ip_due=money(lease.ip_amount),
                        personal_due=money(lease.personal_amount),
                    )
                )
                created += 1
            due = next_due
    session.flush()
    for charge in session.scalars(select(RentCharge)).all():
        update_rent_charge_status(charge)
    return created


def lease_overlaps(lease: Lease, start: date, end: date) -> bool:
    lease_end = lease.end_date or date.max
    return lease.start_date < end and lease_end >= start


def full_months_lived(lease: Lease, end: date) -> int:
    due = effective_due_date(lease.start_date.year, lease.start_date.month, lease.payment_day)
    if due < lease.start_date:
        due = next_due_after(due, lease.payment_day)
    months = 0
    while True:
        next_due = next_due_after(due, lease.payment_day)
        if next_due - timedelta(days=1) <= end:
            months += 1
            due = next_due
        else:
            break
    return months


@dataclass
class ReadingValue:
    value: float
    estimated: bool
    note: str


def reading_value_at(session: Session, meter_id: int, target: date, allow_estimate: bool) -> ReadingValue | None:
    exact = session.scalar(
        select(MeterReading).where(MeterReading.meter_id == meter_id, MeterReading.reading_date == target)
    )
    if exact:
        return ReadingValue(exact.value, False, "")

    before = session.scalar(
        select(MeterReading)
        .where(MeterReading.meter_id == meter_id, MeterReading.reading_date < target)
        .order_by(MeterReading.reading_date.desc())
        .limit(1)
    )
    after = session.scalar(
        select(MeterReading)
        .where(MeterReading.meter_id == meter_id, MeterReading.reading_date > target)
        .order_by(MeterReading.reading_date.asc())
        .limit(1)
    )

    if before and after and allow_estimate:
        days = (after.reading_date - before.reading_date).days
        if days > 0:
            daily = (after.value - before.value) / days
            return ReadingValue(before.value + daily * (target - before.reading_date).days, True, "interpolated")

    if allow_estimate and before:
        previous = session.scalar(
            select(MeterReading)
            .where(MeterReading.meter_id == meter_id, MeterReading.reading_date < before.reading_date)
            .order_by(MeterReading.reading_date.desc())
            .limit(1)
        )
        if previous:
            days = (before.reading_date - previous.reading_date).days
            if days > 0:
                daily = (before.value - previous.value) / days
                return ReadingValue(before.value + daily * (target - before.reading_date).days, True, "forecast")

    return None


def tariff_for_date(session: Session, service_id: int, target: date) -> Tariff | None:
    return session.scalar(
        select(Tariff)
        .where(Tariff.service_id == service_id, Tariff.starts_on <= target)
        .order_by(Tariff.starts_on.desc())
        .limit(1)
    )


def calculate_tiered_cost(consumption: float, tiers_json: str) -> float:
    tiers = json.loads(tiers_json)
    remaining = float(consumption)
    previous_limit = 0.0
    total = 0.0
    for tier in tiers:
        limit = tier.get("limit")
        price = float(tier["price"])
        if limit is None:
            units = remaining
        else:
            units = max(0.0, min(remaining, float(limit) - previous_limit))
        total += units * price
        remaining -= units
        if limit is not None:
            previous_limit = float(limit)
        if remaining <= 0:
            break
    return money(total)


def active_lease_for_apartment(session: Session, apartment_id: int, start: date, end: date) -> Lease | None:
    return session.scalar(
        select(Lease)
        .where(
            Lease.apartment_id == apartment_id,
            Lease.start_date < end,
            or_(Lease.end_date.is_(None), Lease.end_date >= start),
        )
        .order_by(Lease.start_date.desc())
        .limit(1)
    )


def occupied_on(lease: Lease | None, day: date) -> bool:
    if lease is None:
        return False
    return lease.start_date <= day and (lease.end_date is None or lease.end_date >= day)


def allocate_odn(
    apartments: list[Apartment],
    leases_by_apartment: dict[int, Lease | None],
    period_start: date,
    period_end: date,
    odn_consumption: float,
) -> dict[int, float]:
    days = max((period_end - period_start).days, 1)
    weights = {apartment.id: 0.0 for apartment in apartments}
    cursor = period_start
    while cursor < period_end:
        occupied = [apartment for apartment in apartments if occupied_on(leases_by_apartment.get(apartment.id), cursor)]
        total_share = sum(max(apartment.odn_share_percent, 0) for apartment in occupied)
        if total_share > 0:
            for apartment in occupied:
                weights[apartment.id] += max(apartment.odn_share_percent, 0) / total_share
        cursor += timedelta(days=1)
    return {apartment_id: odn_consumption * weight / days for apartment_id, weight in weights.items()}


def calculate_utility_bill(
    session: Session,
    service_id: int,
    period_start: date,
    period_end: date,
    allow_estimate: bool = False,
) -> tuple[UtilityBill, list[str]]:
    if period_end <= period_start:
        raise ValueError("Дата окончания должна быть позже даты начала")

    service = session.get(UtilityService, service_id)
    if not service:
        raise ValueError("Услуга не найдена")

    object_meter = session.scalar(
        select(Meter).where(Meter.service_id == service_id, Meter.scope == "object", Meter.active.is_(True)).limit(1)
    )
    if not object_meter:
        raise ValueError("У услуги нет общедомового счётчика")

    warnings: list[str] = []
    object_start = reading_value_at(session, object_meter.id, period_start, allow_estimate)
    object_end = reading_value_at(session, object_meter.id, period_end, allow_estimate)
    if not object_start or not object_end:
        raise ValueError("Не хватает показаний общедомового счётчика для выбранного периода")
    if object_start.estimated or object_end.estimated:
        warnings.append("Общедомовой расход рассчитан по средним значениям")

    total_consumption = object_end.value - object_start.value
    if total_consumption < 0:
        raise ValueError("Общедомовой счётчик уменьшился. Проверьте показания")

    tariff = tariff_for_date(session, service_id, period_end)
    if not tariff:
        raise ValueError("Не найден тариф на дату окончания периода")

    total_cost = calculate_tiered_cost(total_consumption, tariff.tiers_json)
    average_price = total_cost / total_consumption if total_consumption else 0

    apartments = session.scalars(
        select(Apartment)
        .where(Apartment.object_id == service.object_id, Apartment.active.is_(True))
        .order_by(Apartment.sort_order, Apartment.name)
    ).all()
    leases_by_apartment = {
        apartment.id: active_lease_for_apartment(session, apartment.id, period_start, period_end)
        for apartment in apartments
    }

    personal_by_apartment: dict[int, float] = {}
    apartment_consumption = 0.0
    for apartment in apartments:
        apartment_meter = session.scalar(
            select(Meter)
            .where(
                Meter.service_id == service_id,
                Meter.apartment_id == apartment.id,
                Meter.scope == "apartment",
                Meter.active.is_(True),
            )
            .limit(1)
        )
        personal = 0.0
        if apartment_meter:
            reading_start = reading_value_at(session, apartment_meter.id, period_start, allow_estimate)
            reading_end = reading_value_at(session, apartment_meter.id, period_end, allow_estimate)
            if reading_start and reading_end:
                personal = reading_end.value - reading_start.value
                if personal < 0:
                    raise ValueError(f"Счётчик {apartment.name} уменьшился. Проверьте показания")
                if reading_start.estimated or reading_end.estimated:
                    warnings.append(f"{apartment.name}: личный расход рассчитан по средним значениям")
            elif not allow_estimate:
                raise ValueError(f"Не хватает показаний по квартире {apartment.name}")
        personal_by_apartment[apartment.id] = personal
        apartment_consumption += personal

    if apartment_consumption - total_consumption > 0.001:
        raise ValueError("Сумма квартирных счётчиков больше общедомового расхода. Тут бухгалтерия сказала 'стоп'.")

    odn_consumption = max(0.0, total_consumption - apartment_consumption)
    odn_by_apartment = allocate_odn(apartments, leases_by_apartment, period_start, period_end, odn_consumption)

    bill = UtilityBill(
        service_id=service_id,
        period_start=period_start,
        period_end=period_end,
        status="draft",
        total_consumption=money(total_consumption),
        apartment_consumption=money(apartment_consumption),
        odn_consumption=money(odn_consumption),
        total_cost=total_cost,
        average_unit_price=round(average_price, 4),
        is_forecast=allow_estimate,
        notes="\n".join(warnings),
    )

    for apartment in apartments:
        lease = leases_by_apartment.get(apartment.id)
        if not lease:
            continue
        personal = personal_by_apartment.get(apartment.id, 0.0)
        odn = odn_by_apartment.get(apartment.id, 0.0)
        bill.lines.append(
            UtilityBillLine(
                apartment_id=apartment.id,
                lease_id=lease.id,
                personal_consumption=money(personal),
                odn_consumption=money(odn),
                total_amount=money((personal + odn) * average_price),
                status="draft",
            )
        )

    return bill, warnings


def resident_due_date(service: UtilityService, today: date | None = None) -> date:
    today = today or date.today()
    proposed = today + timedelta(days=service.resident_due_days)
    _, last_day = calendar.monthrange(today.year, today.month)
    provider_day = min(service.provider_due_day, last_day)
    provider_due = date(today.year, today.month, provider_day)
    if today <= provider_due:
        return min(proposed, provider_due)
    return proposed


def update_utility_line_status(line: UtilityBillLine, today: date | None = None) -> str:
    today = today or date.today()
    if line.paid_amount + 0.009 >= line.total_amount:
        line.status = "paid_ahead" if line.paid_amount > line.total_amount + 0.009 else "paid"
    elif line.paid_amount > 0:
        line.status = "partial"
    elif line.due_date and line.due_date < today:
        line.status = "overdue"
    elif line.status not in {"draft", "issued"}:
        line.status = "issued"
    return line.status


def object_last_reading_date(session: Session, service: UtilityService) -> date | None:
    return session.scalar(
        select(func.max(MeterReading.reading_date))
        .join(Meter, MeterReading.meter_id == Meter.id)
        .where(Meter.service_id == service.id, Meter.scope == "object")
    )
