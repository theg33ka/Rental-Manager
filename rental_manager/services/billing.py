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
IGNORE_LEASE_MARK = "IGNORE_LEASE_CONTROL"
LEGACY_PERSONAL_IGNORE_BEFORE = date(2026, 4, 1)
RENT_GENERATION_START = date(2025, 1, 1)


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
    if due <= 0:
        return "paid_ahead" if paid > 0.009 else "paid"
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
    total_due = float(charge.ip_due or 0) + float(charge.personal_due or 0)
    total_paid = ip_paid + personal_paid

    if ip_status in {"paid", "paid_ahead"} and personal_status in {"paid", "paid_ahead"}:
        charge.status = "paid_ahead" if total_paid > total_due + 0.009 else "paid"
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
    leases = session.scalars(select(Lease).join(Apartment).where(Lease.active.is_(True), Apartment.active.is_(True))).all()
    for lease in leases:
        if IGNORE_LEASE_MARK in (lease.notes or ""):
            continue
        due = effective_due_date(lease.start_date.year, lease.start_date.month, lease.payment_day)
        if due < lease.start_date:
            due = next_due_after(due, lease.payment_day)
        while due < RENT_GENERATION_START:
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
            else:
                can_sync_future_amounts = (
                    exists.due_date >= date.today()
                    and money(float(exists.ip_paid or 0)) <= 0.009
                    and money(float(exists.personal_paid or 0)) <= 0.009
                )
                if can_sync_future_amounts:
                    exists.ip_due = money(lease.ip_amount)
                    exists.personal_due = money(lease.personal_amount)
                elif money(exists.ip_due) <= 0 and money(lease.ip_amount) > 0:
                    exists.ip_due = money(lease.ip_amount)
                if not can_sync_future_amounts and money(exists.personal_due) <= 0 and money(lease.personal_amount) > 0:
                    exists.personal_due = money(lease.personal_amount)
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


@dataclass
class UtilitySegment:
    apartment: Apartment
    lease: Lease
    start: date
    end: date
    personal_consumption: float
    odn_consumption: float


def lease_for_day(leases: list[Lease], day: date) -> Lease | None:
    for lease in leases:
        if lease.start_date <= day and (lease.end_date is None or lease.end_date >= day):
            return lease
    return None


def overlap_days(start: date, end: date, other_start: date, other_end: date) -> int:
    return max(0, (min(end, other_end) - max(start, other_start)).days)


def segment_label(start: date, end: date) -> str:
    return f"{start:%d.%m.%Y} -> {end:%d.%m.%Y} ({(end - start).days} \u0434\u043d.)"


def reading_or_error(
    session: Session,
    meter_id: int,
    target: date,
    *,
    allow_estimate: bool,
    warning_prefix: str,
    warnings: list[str],
) -> ReadingValue:
    reading = reading_value_at(session, meter_id, target, allow_estimate)
    if not reading:
        raise ValueError(f"\u041d\u0435 \u0445\u0432\u0430\u0442\u0430\u0435\u0442 \u043f\u043e\u043a\u0430\u0437\u0430\u043d\u0438\u0439: {warning_prefix} \u043d\u0430 {target:%d.%m.%Y}")
    if reading.estimated:
        warnings.append(f"{warning_prefix}: \u0438\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u043d\u043e \u0440\u0430\u0441\u0447\u0451\u0442\u043d\u043e\u0435 \u0437\u043d\u0430\u0447\u0435\u043d\u0438\u0435 \u043d\u0430 {target:%d.%m.%Y}")
    return reading


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
    leases = session.scalars(
        select(Lease)
        .where(
            Lease.apartment_id == apartment_id,
            Lease.start_date < end,
            or_(Lease.end_date.is_(None), Lease.end_date >= start),
        )
        .order_by(Lease.start_date.desc())
    ).all()
    return next((lease for lease in leases if IGNORE_LEASE_MARK not in (lease.notes or "")), None)


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
        raise ValueError("\u0414\u0430\u0442\u0430 \u043e\u043a\u043e\u043d\u0447\u0430\u043d\u0438\u044f \u0434\u043e\u043b\u0436\u043d\u0430 \u0431\u044b\u0442\u044c \u043f\u043e\u0437\u0436\u0435 \u0434\u0430\u0442\u044b \u043d\u0430\u0447\u0430\u043b\u0430")

    service = session.get(UtilityService, service_id)
    if not service:
        raise ValueError("\u0423\u0441\u043b\u0443\u0433\u0430 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430")

    object_meter = session.scalar(
        select(Meter).where(Meter.service_id == service_id, Meter.scope == "object", Meter.active.is_(True)).limit(1)
    )
    if not object_meter:
        raise ValueError("\u0423 \u0443\u0441\u043b\u0443\u0433\u0438 \u043d\u0435\u0442 \u043e\u0431\u0449\u0435\u0434\u043e\u043c\u043e\u0432\u043e\u0433\u043e \u0441\u0447\u0451\u0442\u0447\u0438\u043a\u0430")

    warnings: list[str] = []
    object_start = reading_or_error(
        session,
        object_meter.id,
        period_start,
        allow_estimate=allow_estimate,
        warning_prefix=f"{service.object.name}: \u043e\u0431\u0449\u0435\u0434\u043e\u043c\u043e\u0432\u043e\u0439 \u0441\u0447\u0451\u0442\u0447\u0438\u043a ({service.name})",
        warnings=warnings,
    )
    object_end = reading_or_error(
        session,
        object_meter.id,
        period_end,
        allow_estimate=allow_estimate,
        warning_prefix=f"{service.object.name}: \u043e\u0431\u0449\u0435\u0434\u043e\u043c\u043e\u0432\u043e\u0439 \u0441\u0447\u0451\u0442\u0447\u0438\u043a ({service.name})",
        warnings=warnings,
    )

    total_consumption = object_end.value - object_start.value
    if total_consumption < 0:
        raise ValueError("\u041e\u0431\u0449\u0435\u0434\u043e\u043c\u043e\u0432\u043e\u0439 \u0441\u0447\u0451\u0442\u0447\u0438\u043a \u0443\u043c\u0435\u043d\u044c\u0448\u0438\u043b\u0441\u044f. \u041f\u0440\u043e\u0432\u0435\u0440\u044c\u0442\u0435 \u043f\u043e\u043a\u0430\u0437\u0430\u043d\u0438\u044f")

    tariff = tariff_for_date(session, service_id, period_end)
    if not tariff:
        raise ValueError("\u041d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d \u0442\u0430\u0440\u0438\u0444 \u043d\u0430 \u0434\u0430\u0442\u0443 \u043e\u043a\u043e\u043d\u0447\u0430\u043d\u0438\u044f \u043f\u0435\u0440\u0438\u043e\u0434\u0430")

    total_cost = calculate_tiered_cost(total_consumption, tariff.tiers_json)
    average_price = total_cost / total_consumption if total_consumption else 0.0

    all_object_apartments = session.scalars(
        select(Apartment).where(Apartment.object_id == service.object_id).order_by(Apartment.sort_order, Apartment.name)
    ).all()
    inactive_apartment_names = [apartment.name for apartment in all_object_apartments if not apartment.active]

    apartments = session.scalars(
        select(Apartment)
        .where(Apartment.object_id == service.object_id, Apartment.active.is_(True))
        .order_by(Apartment.sort_order, Apartment.name)
    ).all()

    apartment_leases: dict[int, list[Lease]] = {
        apartment.id: [
            lease
            for lease in session.scalars(
                select(Lease)
                .where(
                    Lease.apartment_id == apartment.id,
                    Lease.start_date < period_end,
                    or_(Lease.end_date.is_(None), Lease.end_date >= period_start),
                )
                .order_by(Lease.start_date)
            ).all()
            if IGNORE_LEASE_MARK not in (lease.notes or "")
        ]
        for apartment in apartments
    }
    vacant_apartment_names = [apartment.name for apartment in apartments if not apartment_leases.get(apartment.id) and apartment.leases]

    apartment_meters: dict[int, Meter] = {}
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
        if apartment_meter:
            apartment_meters[apartment.id] = apartment_meter

    existing_lines = session.scalars(
        select(UtilityBillLine)
        .join(UtilityBill)
        .where(
            UtilityBill.service_id == service_id,
            UtilityBill.status != "draft",
            UtilityBill.period_start < period_end,
            UtilityBill.period_end > period_start,
        )
    ).all()

    covered_intervals: dict[tuple[int, int], list[tuple[date, date]]] = {}
    boundaries = {period_start, period_end}
    previously_billed_amount = 0.0
    billed_segment_count = 0

    if allow_estimate:
        for apartment_meter in apartment_meters.values():
            reading_dates = session.scalars(
                select(MeterReading.reading_date).where(
                    MeterReading.meter_id == apartment_meter.id,
                    MeterReading.reading_date > period_start,
                    MeterReading.reading_date < period_end,
                )
            ).all()
            boundaries.update(reading_dates)

    for leases in apartment_leases.values():
        for lease in leases:
            if period_start < lease.start_date < period_end:
                boundaries.add(lease.start_date)
            if lease.end_date:
                next_day = lease.end_date + timedelta(days=1)
                if period_start < next_day < period_end:
                    boundaries.add(next_day)

    for line in existing_lines:
        if not line.lease_id:
            continue
        overlap_start = max(period_start, line.bill.period_start)
        overlap_end = min(period_end, line.bill.period_end)
        if overlap_end <= overlap_start:
            continue
        boundaries.add(overlap_start)
        boundaries.add(overlap_end)
        covered_intervals.setdefault((line.apartment_id, line.lease_id), []).append((overlap_start, overlap_end))
        full_days = max((line.bill.period_end - line.bill.period_start).days, 1)
        overlap_ratio = overlap_days(period_start, period_end, line.bill.period_start, line.bill.period_end) / full_days
        previously_billed_amount += line.total_amount * overlap_ratio

    sorted_boundaries = sorted(boundaries)
    segments: list[UtilitySegment] = []
    all_apartment_consumption_total = 0.0

    for start, end in zip(sorted_boundaries, sorted_boundaries[1:]):
        if end <= start:
            continue

        object_segment_start = reading_or_error(
            session,
            object_meter.id,
            start,
            allow_estimate=True,
            warning_prefix=f"{service.object.name}: \u043e\u0431\u0449\u0435\u0434\u043e\u043c\u043e\u0432\u043e\u0439 \u0441\u0447\u0451\u0442\u0447\u0438\u043a ({service.name})",
            warnings=warnings,
        )
        object_segment_end = reading_or_error(
            session,
            object_meter.id,
            end,
            allow_estimate=True,
            warning_prefix=f"{service.object.name}: \u043e\u0431\u0449\u0435\u0434\u043e\u043c\u043e\u0432\u043e\u0439 \u0441\u0447\u0451\u0442\u0447\u0438\u043a ({service.name})",
            warnings=warnings,
        )
        object_segment_consumption = object_segment_end.value - object_segment_start.value
        if object_segment_consumption < 0:
            raise ValueError("\u041e\u0431\u0449\u0435\u0434\u043e\u043c\u043e\u0432\u043e\u0439 \u0441\u0447\u0451\u0442\u0447\u0438\u043a \u0443\u043c\u0435\u043d\u044c\u0448\u0438\u043b\u0441\u044f \u0432\u043d\u0443\u0442\u0440\u0438 \u0432\u044b\u0431\u0440\u0430\u043d\u043d\u043e\u0433\u043e \u043f\u0435\u0440\u0438\u043e\u0434\u0430. \u041f\u0440\u043e\u0432\u0435\u0440\u044c\u0442\u0435 \u043f\u043e\u043a\u0430\u0437\u0430\u043d\u0438\u044f.")

        active_leases = {apartment.id: lease_for_day(apartment_leases[apartment.id], start) for apartment in apartments}
        personal_by_apartment: dict[int, float] = {}
        segment_apartment_consumption = 0.0

        for apartment in apartments:
            personal = 0.0
            apartment_meter = apartment_meters.get(apartment.id)
            if apartment_meter:
                reading_start = reading_value_at(session, apartment_meter.id, start, True)
                reading_end = reading_value_at(session, apartment_meter.id, end, True)
                if reading_start and reading_end:
                    personal = reading_end.value - reading_start.value
                    if personal < 0:
                        raise ValueError(f"\u0421\u0447\u0451\u0442\u0447\u0438\u043a {apartment.name} \u0443\u043c\u0435\u043d\u044c\u0448\u0438\u043b\u0441\u044f. \u041f\u0440\u043e\u0432\u0435\u0440\u044c\u0442\u0435 \u043f\u043e\u043a\u0430\u0437\u0430\u043d\u0438\u044f")
                    if reading_start.estimated:
                        warnings.append(f"{apartment.name}: \u0440\u0430\u0441\u0447\u0451\u0442\u043d\u043e\u0435 \u043b\u0438\u0447\u043d\u043e\u0435 \u043f\u043e\u043a\u0430\u0437\u0430\u043d\u0438\u0435 \u043d\u0430 {start:%d.%m.%Y}")
                    if reading_end.estimated:
                        warnings.append(f"{apartment.name}: \u0440\u0430\u0441\u0447\u0451\u0442\u043d\u043e\u0435 \u043b\u0438\u0447\u043d\u043e\u0435 \u043f\u043e\u043a\u0430\u0437\u0430\u043d\u0438\u0435 \u043d\u0430 {end:%d.%m.%Y}")
                elif not allow_estimate:
                    raise ValueError(f"\u041d\u0435 \u0445\u0432\u0430\u0442\u0430\u0435\u0442 \u043f\u043e\u043a\u0430\u0437\u0430\u043d\u0438\u0439 \u043f\u043e \u043a\u0432\u0430\u0440\u0442\u0438\u0440\u0435 {apartment.name}")

            personal = money(personal)
            personal_by_apartment[apartment.id] = personal
            segment_apartment_consumption += personal

        all_apartment_consumption_total += segment_apartment_consumption
        if segment_apartment_consumption - object_segment_consumption > 0.001:
            raise ValueError("\u0421\u0443\u043c\u043c\u0430 \u043a\u0432\u0430\u0440\u0442\u0438\u0440\u043d\u044b\u0445 \u0441\u0447\u0451\u0442\u0447\u0438\u043a\u043e\u0432 \u0431\u043e\u043b\u044c\u0448\u0435 \u043e\u0431\u0449\u0435\u0434\u043e\u043c\u043e\u0432\u043e\u0433\u043e \u0440\u0430\u0441\u0445\u043e\u0434\u0430. \u0422\u0443\u0442 \u0431\u0443\u0445\u0433\u0430\u043b\u0442\u0435\u0440\u0438\u044f \u0441\u043a\u0430\u0437\u0430\u043b\u0430 '\u0441\u0442\u043e\u043f'.")

        segment_odn = max(0.0, object_segment_consumption - segment_apartment_consumption)
        occupied_apartments = [apartment for apartment in apartments if active_leases.get(apartment.id)]
        total_share = sum(max(apartment.odn_share_percent, 0) for apartment in occupied_apartments)

        for apartment in occupied_apartments:
            lease = active_leases[apartment.id]
            assert lease is not None
            already_covered = any(
                interval_start <= start and interval_end >= end
                for interval_start, interval_end in covered_intervals.get((apartment.id, lease.id), [])
            )
            if already_covered:
                billed_segment_count += 1
                continue

            personal = personal_by_apartment.get(apartment.id, 0.0)
            odn = money(segment_odn * max(apartment.odn_share_percent, 0) / total_share) if total_share > 0 else 0.0
            segments.append(
                UtilitySegment(
                    apartment=apartment,
                    lease=lease,
                    start=start,
                    end=end,
                    personal_consumption=personal,
                    odn_consumption=odn,
                )
            )

    if all_apartment_consumption_total - total_consumption > 0.001:
        raise ValueError("\u0421\u0443\u043c\u043c\u0430 \u043a\u0432\u0430\u0440\u0442\u0438\u0440\u043d\u044b\u0445 \u0441\u0447\u0451\u0442\u0447\u0438\u043a\u043e\u0432 \u0431\u043e\u043b\u044c\u0448\u0435 \u043e\u0431\u0449\u0435\u0434\u043e\u043c\u043e\u0432\u043e\u0433\u043e \u0440\u0430\u0441\u0445\u043e\u0434\u0430. \u0422\u0443\u0442 \u0431\u0443\u0445\u0433\u0430\u043b\u0442\u0435\u0440\u0438\u044f \u0441\u043a\u0430\u0437\u0430\u043b\u0430 '\u0441\u0442\u043e\u043f'.")

    if not segments and previously_billed_amount > 0:
        raise ValueError("\u0417\u0430 \u0432\u044b\u0431\u0440\u0430\u043d\u043d\u044b\u0439 \u043f\u0435\u0440\u0438\u043e\u0434 \u0443\u0436\u0435 \u0435\u0441\u0442\u044c \u0432\u044b\u0441\u0442\u0430\u0432\u043b\u0435\u043d\u043d\u044b\u0435 \u043a\u043e\u043c\u043c\u0443\u043d\u0430\u043b\u044c\u043d\u044b\u0435 \u0441\u0447\u0435\u0442\u0430. \u0423\u0434\u0430\u043b\u0438\u0442\u0435 \u0441\u0442\u0430\u0440\u044b\u0439 \u0447\u0435\u0440\u043d\u043e\u0432\u0438\u043a \u0438\u043b\u0438 \u0432\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0434\u0440\u0443\u0433\u043e\u0439 \u0438\u043d\u0442\u0435\u0440\u0432\u0430\u043b.")

    house_odn_consumption = max(0.0, total_consumption - all_apartment_consumption_total)
    notes = list(dict.fromkeys(warnings))
    if inactive_apartment_names:
        notes.append("Выключены из учёта: " + ", ".join(inactive_apartment_names) + ".")
    if vacant_apartment_names:
        notes.append("Без жильца в выбранном периоде: " + ", ".join(vacant_apartment_names) + ".")
    if previously_billed_amount > 0:
        notes.append(f"\u0420\u0430\u043d\u0435\u0435 \u0436\u0438\u043b\u044c\u0446\u0430\u043c \u0443\u0436\u0435 \u0432\u044b\u0441\u0442\u0430\u0432\u043b\u044f\u043b\u043e\u0441\u044c \u043f\u0440\u0438\u043c\u0435\u0440\u043d\u043e \u043d\u0430 {money(previously_billed_amount):.2f} \u20bd.")
    if billed_segment_count:
        notes.append(f"\u041f\u0440\u043e\u043f\u0443\u0449\u0435\u043d\u043e \u0443\u0436\u0435 \u0432\u044b\u0441\u0442\u0430\u0432\u043b\u0435\u043d\u043d\u044b\u0445 \u0441\u0435\u0433\u043c\u0435\u043d\u0442\u043e\u0432: {billed_segment_count}.")

    bill = UtilityBill(
        service_id=service_id,
        period_start=period_start,
        period_end=period_end,
        status="draft",
        total_consumption=money(total_consumption),
        apartment_consumption=money(all_apartment_consumption_total),
        odn_consumption=money(house_odn_consumption),
        total_cost=total_cost,
        average_unit_price=round(average_price, 4),
        is_forecast=allow_estimate,
        notes="\n".join(notes),
    )

    for segment in segments:
        bill.lines.append(
            UtilityBillLine(
                apartment_id=segment.apartment.id,
                lease_id=segment.lease.id,
                personal_consumption=money(segment.personal_consumption),
                odn_consumption=money(segment.odn_consumption),
                total_amount=money((segment.personal_consumption + segment.odn_consumption) * average_price),
                status="draft",
                note=segment_label(segment.start, segment.end),
            )
        )

    return bill, notes


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
    if line.status == "cancelled":
        return line.status
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
