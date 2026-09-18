from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from rental_manager.models import Apartment, Lease, RentalObject, RentCharge, UtilityBill, UtilityBillLine, UtilityService
from rental_manager.services.billing import money, utility_line_period

MAX_CALENDAR_DAYS = 124
STATUS_LABELS = {
    "paid": "Оплачено",
    "issued": "Счёт выставлен",
    "partial": "Оплачено частично",
    "overdue": "Просрочено",
    "deferred": "Отсрочка",
    "draft": "Черновик",
    "vacant": "Пустует",
    "unbilled": "Не начислено",
    "incomplete": "Не всё начислено",
    "conflict": "Проверить договоры",
    "recent": "Можно выставить",
    "to_bill": "Пора выставить",
    "gap": "Пропуск оплаты",
}


def calendar_charge_status(debt: float, paid: float, due: date | None, today: date, *, draft: bool = False, deferred: date | None = None) -> str:
    if draft:
        return "draft"
    if debt <= 0.009:
        return "paid"
    if deferred and deferred >= today:
        return "deferred"
    if due and due < today:
        return "overdue"
    return "partial" if paid > 0.009 else "issued"


def _calendar_data(
    session: Session,
    start: date,
    end: date,
    *,
    mode: str = "utility",
    today: date | None = None,
    ignored_lease_ids: set[int] | None = None,
) -> dict[str, Any]:
    if mode not in {"utility", "rent"}:
        raise ValueError("Выберите аренду или коммуналку")
    today = today or date.today()
    stop = end + timedelta(days=1)
    objects = session.scalars(select(RentalObject).order_by(RentalObject.id)).all()
    apartments = session.scalars(select(Apartment).order_by(Apartment.sort_order, Apartment.name, Apartment.id)).all()
    leases = session.scalars(
        select(Lease).options(joinedload(Lease.tenant))
        .where(Lease.start_date <= end, or_(Lease.end_date.is_(None), Lease.end_date >= start))
        .order_by(Lease.start_date, Lease.id)
    ).all()
    stays: dict[int, list[Lease]] = defaultdict(list)
    for lease in leases:
        stays[lease.apartment_id].append(lease)
    entries: dict[int, list[dict[str, Any]]] = defaultdict(list)
    services: dict[int, dict[int, str]] = defaultdict(dict)
    for service in session.scalars(select(UtilityService).where(UtilityService.active.is_(True))).all():
        services[service.object_id][service.id] = service.name

    if mode == "utility":
        lines = session.scalars(
            select(UtilityBillLine)
            .join(UtilityBill)
            .options(joinedload(UtilityBillLine.bill).joinedload(UtilityBill.service), joinedload(UtilityBillLine.lease))
            .where(UtilityBill.period_start < stop, UtilityBill.period_end > start, UtilityBill.status != "cancelled", UtilityBillLine.status != "cancelled")
            .order_by(UtilityBill.period_start, UtilityBillLine.id)
        ).all()
        for line in lines:
            line_start, line_end = utility_line_period(line)
            if line_end <= line_start:
                continue
            advance = line.line_type == "advance" or line.bill.bill_type == "advance"
            draft = line.bill.status == "draft" or line.status == "draft"
            paid = money(line.paid_amount)
            debt = money(max(0, line.total_amount - paid))
            due_date = line.due_date or line.bill.due_date
            entries[line.apartment_id].append({
                "id": f"utility:{line.id}", "bill_id": line.bill_id, "lease_id": line.lease_id,
                "kind": "advance" if advance else "usage", "service_id": line.bill.service_id,
                "title": "Аванс коммуналки" if advance else line.bill.service.name,
                "start": line_start.isoformat(), "end": line_end.isoformat(),
                "amount": money(line.total_amount), "paid": paid, "debt": debt,
                "due_date": due_date.isoformat() if due_date else None,
                "status": calendar_charge_status(debt, paid, due_date, today, draft=draft),
                "forecast": line.bill.is_forecast, "note": line.note or "",
            })
    else:
        charges = session.scalars(
            select(RentCharge).join(Lease).options(joinedload(RentCharge.lease))
            .where(RentCharge.period_start < stop, RentCharge.period_end >= start)
            .order_by(RentCharge.period_start, RentCharge.id)
        ).all()
        for charge in charges:
            lease = charge.lease
            if charge.status == "cancelled":
                continue
            line_start = max(charge.period_start, lease.start_date)
            line_end = min(charge.period_end, lease.end_date or date.max)
            if line_end < line_start or line_end < start or line_start > end:
                continue
            debt = money(max(0, charge.ip_due - charge.ip_paid) + max(0, charge.personal_due - charge.personal_paid))
            paid = money(charge.ip_paid + charge.personal_paid)
            entries[lease.apartment_id].append({
                "id": f"rent:{charge.id}", "lease_id": lease.id, "kind": "rent", "title": "Аренда",
                "start": line_start.isoformat(), "end": (line_end + timedelta(days=1)).isoformat(),
                "period_start": charge.period_start.isoformat(), "period_end": charge.period_end.isoformat(),
                "amount": money(charge.ip_due + charge.personal_due), "paid": paid, "debt": debt,
                "ip_due": charge.ip_due, "ip_paid": charge.ip_paid,
                "personal_due": charge.personal_due, "personal_paid": charge.personal_paid,
                "due_date": charge.due_date.isoformat(),
                "deferred_until": charge.deferral_until.isoformat() if charge.deferral_until else None,
                "status": calendar_charge_status(debt, paid, charge.due_date, today, deferred=charge.deferral_until),
                "forecast": False,
            })

    paid_until: dict[tuple[int, int], str] = {}
    if mode == "utility":
        paid_lines = session.scalars(
            select(UtilityBillLine).join(UtilityBill)
            .options(joinedload(UtilityBillLine.bill), joinedload(UtilityBillLine.lease))
            .where(UtilityBill.status.notin_(["draft", "cancelled"]),
                   UtilityBillLine.status.notin_(["draft", "cancelled"]),
                   UtilityBill.bill_type != "advance", UtilityBillLine.line_type != "advance",
                   UtilityBillLine.lease_id.in_([lease.id for lease in leases]),
                   UtilityBillLine.paid_amount + 0.009 >= UtilityBillLine.total_amount)
        ).all()
        for paid_line in paid_lines:
            if not paid_line.lease_id:
                continue
            paid_start, paid_end = utility_line_period(paid_line)
            if paid_end > paid_start:
                key = (paid_line.lease_id, paid_line.bill.service_id)
                paid_until[key] = max(paid_until.get(key, ""), paid_end.isoformat())
    return {"objects": objects, "apartments": apartments, "stays": stays, "entries": entries, "services": services, "paid_until": paid_until}


def calendar_day(apartment_id: int, object_id: int, day: str, snapshot: dict[str, Any], mode: str, today: date) -> dict[str, Any]:
    occupants = [lease for lease in snapshot["stays"][apartment_id] if lease.start_date.isoformat() <= day and (not lease.end_date or day <= lease.end_date.isoformat())]
    occupants_ids = {lease.id for lease in occupants}
    selected = [entry for entry in snapshot["entries"][apartment_id] if entry["start"] <= day < entry["end"]]
    usage = [entry for entry in selected if entry["kind"] != "advance"]
    expected = snapshot["services"][object_id]
    billed_services = {entry.get("service_id") for entry in usage if entry["status"] != "draft" and entry["lease_id"] in occupants_ids}
    missing = [name for key, name in expected.items() if key not in billed_services] if mode == "utility" and occupants else []
    states = {entry["status"] for entry in usage}
    conflict = len(occupants) > 1 or any(entry["lease_id"] not in occupants_ids for entry in usage)
    if conflict:
        status = "conflict"
    elif not occupants:
        status = "vacant"
    else:
        status = next((candidate for candidate in ["overdue", "partial", "issued", "deferred", "draft"] if candidate in states), "unbilled" if not usage else "incomplete" if missing else "paid")
        if mode == "utility" and status in {"unbilled", "incomplete"} and day <= today.isoformat():
            gap = any(service_id not in billed_services and snapshot["paid_until"].get((lease.id, service_id), "") > day for lease in occupants for service_id in expected)
            # Позднее оплаченный период того же договора выявляет пропуск по той же услуге.
            if not expected and not usage:
                gap = any(lease_id in occupants_ids and paid_end > day for (lease_id, _), paid_end in snapshot["paid_until"].items())
            status = "gap" if gap else "to_bill" if day < (today - timedelta(days=31)).isoformat() else "incomplete" if status == "incomplete" else "recent"
    return {
        "date": day, "status": status, "lease_ids": sorted(occupants_ids),
        "entry_ids": [entry["id"] for entry in selected], "missing_services": missing,
        "move_in": any(lease.start_date.isoformat() == day for lease in occupants),
        "move_out": any(lease.end_date and lease.end_date.isoformat() == day for lease in occupants),
        "forecast": any(entry["forecast"] for entry in usage),
    }


def calendar_periods(entries: list[dict[str, Any]], today: date) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        groups[(entry.get("bill_id", entry["id"]), entry["lease_id"], entry["kind"])].append(entry)
    result = []
    for grouped in groups.values():
        runs: list[list[dict[str, Any]]] = []
        for entry in sorted(grouped, key=lambda item: (item["start"], item["end"])):
            if runs and entry["start"] <= max(item["end"] for item in runs[-1]):
                runs[-1].append(entry)
            else:
                runs.append([entry])
        for run in runs:
            first = run[0]
            start, end = min(item["start"] for item in run), max(item["end"] for item in run)
            amount, paid, debt = (money(sum(item[key] for item in run)) for key in ["amount", "paid", "debt"])
            statuses = {item["status"] for item in run}
            status = next((value for value in ["overdue", "partial", "issued", "deferred", "draft"] if value in statuses), "paid")
            result.append({
                "id": f"{first['id']}:{start}", "entry_ids": [item["id"] for item in run],
                "lease_id": first["lease_id"], "kind": first["kind"], "title": first["title"],
                "start": start, "end": end, "days": (date.fromisoformat(end)-date.fromisoformat(start)).days,
                "amount": amount, "paid": paid, "debt": debt, "status": status,
                "forecast": any(item["forecast"] for item in run),
            })
    return sorted(result, key=lambda item: (item["start"], item["end"], item["id"]))


def payment_calendar(session: Session, start: date, end: date, *, mode: str = "utility", today: date | None = None, ignored_lease_ids: set[int] | None = None) -> dict[str, Any]:
    if end < start or (end - start).days >= MAX_CALENDAR_DAYS or end == date.max:
        raise ValueError(f"Выберите период от 1 до {MAX_CALENDAR_DAYS} дней")
    today = today or date.today()
    snapshot = _calendar_data(session, start, end, mode=mode, today=today, ignored_lease_ids=ignored_lease_ids)
    dates = [(start + timedelta(days=offset)).isoformat() for offset in range((end-start).days + 1)]
    rows_by_object: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for apartment in snapshot["apartments"]:
        apartment_stays = snapshot["stays"][apartment.id]
        apartment_entries = snapshot["entries"][apartment.id]
        days = [calendar_day(apartment.id, apartment.object_id, day, snapshot, mode, today) for day in dates]
        rows_by_object[apartment.object_id].append({
            "id": apartment.id, "name": apartment.name, "active": apartment.active,
            "leases": [{"id": lease.id, "tenant": lease.tenant.full_name, "start": lease.start_date.isoformat(), "end": lease.end_date.isoformat() if lease.end_date else None} for lease in apartment_stays],
            "entries": apartment_entries, "periods": calendar_periods(apartment_entries, today), "days": days,
        })
    return {
        "start": start.isoformat(), "end": end.isoformat(), "today": today.isoformat(), "mode": mode,
        "dates": dates, "statuses": STATUS_LABELS,
        "objects": [{"id": obj.id, "name": obj.name, "active": obj.active, "apartments": rows_by_object[obj.id]} for obj in snapshot["objects"]],
    }


def payment_calendar_summary(session: Session, *, mode: str = "utility", today: date | None = None, ignored_lease_ids: set[int] | None = None) -> dict[str, Any]:
    today = today or date.today()
    snapshot = _calendar_data(session, date.min, today, mode=mode, today=today, ignored_lease_ids=ignored_lease_ids)
    result = []
    for apartment in snapshot["apartments"]:
        boundaries = {today.isoformat(), (today + timedelta(days=1)).isoformat(), (today - timedelta(days=31)).isoformat()}
        for lease in snapshot["stays"][apartment.id]:
            boundaries.add(lease.start_date.isoformat())
            if lease.end_date and lease.end_date < today:
                boundaries.add((lease.end_date + timedelta(days=1)).isoformat())
        for entry in snapshot["entries"][apartment.id]:
            boundaries.update([entry["start"], entry["end"]])
        ordered = sorted(day for day in boundaries if day <= (today + timedelta(days=1)).isoformat())
        issues: list[dict[str, str]] = []
        for start, end in zip(ordered, ordered[1:]):
            state = calendar_day(apartment.id, apartment.object_id, start, snapshot, mode, today)
            if state["status"] not in {"gap", "overdue"}:
                continue
            if issues and issues[-1]["end"] == start and issues[-1]["status"] == state["status"]:
                issues[-1]["end"] = end
            else:
                issues.append({"start": start, "end": end, "status": state["status"]})
        result.append({"apartment_id": apartment.id, "issues": issues})
    return {"today": today.isoformat(), "mode": mode, "apartments": result}
