from __future__ import annotations

from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from rental_manager.models import Lease, UtilityBill, UtilityBillLine
from rental_manager.services.billing import money, utility_line_period


def advance_owner_lease_ids(session: Session, apartment_id: int, lease_id: int | None):
    lease = session.get(Lease, lease_id) if lease_id else None
    return select(Lease.id).where(
        Lease.apartment_id == apartment_id,
        Lease.tenant_id == (lease.tenant_id if lease and lease.apartment_id == apartment_id else -1),
    )


def advance_owner_key(lease: Lease | None, apartment_id: int) -> str:
    return f"{apartment_id}:{lease.tenant_id if lease else 'unassigned'}"


def covered_days(ranges: list[tuple[date, date]]) -> int:
    merged: list[list[date]] = []
    for start, end in sorted(ranges):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return sum((end - start).days for start, end in merged)


def monthly_history(lines: list[UtilityBillLine], target_days: int) -> tuple[dict[tuple[int, int], float], int]:
    samples: dict[tuple[int, int, int], list[UtilityBillLine]] = defaultdict(list)
    excluded = 0
    for line in lines:
        start, end = utility_line_period(line)
        if (line.status == "cancelled" or line.bill.status in {"draft", "cancelled"}
                or not 0 < (end - start).days <= 45
                or (line.bill.period_end - line.bill.period_start).days > 45):
            excluded += 1
            continue
        samples[(line.bill.service_id, start.year, start.month)].append(line)
    monthly: dict[tuple[int, int], float] = {}
    for (_service, year, month), items in samples.items():
        days = covered_days([utility_line_period(line) for line in items])
        if days < 14:
            excluded += len(items)
            continue
        key = (year, month)
        monthly[key] = money(monthly.get(key, 0) + sum(line.total_amount for line in items) * target_days / days)
    return monthly, excluded


def usage_forecasts(bills: list[UtilityBill], target_days: int) -> dict[int, float]:
    samples: dict[tuple[int, int], list[UtilityBillLine]] = defaultdict(list)
    for bill in bills:
        if bill.bill_type == "advance" or bill.status == "cancelled":
            continue
        for line in bill.lines:
            if line.line_type != "advance" and line.status != "cancelled":
                samples[(line.apartment_id, bill.service_id)].append(line)
    amounts: dict[int, float] = {}
    for (apartment_id, _service), items in samples.items():
        days = covered_days([utility_line_period(line) for line in items])
        if days:
            amounts[apartment_id] = money(amounts.get(apartment_id, 0) + sum(line.total_amount for line in items) * target_days / days)
    return amounts
