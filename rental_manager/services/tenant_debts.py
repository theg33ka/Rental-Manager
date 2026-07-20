from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from rental_manager.models import Lease
from rental_manager.services.billing import IGNORE_LEASE_MARK


def tenant_debt_leases(
    session: Session,
    lease: Lease,
    ignored_lease_ids: Iterable[int] = (),
) -> list[Lease]:
    ignored = set(ignored_lease_ids)
    leases = session.scalars(
        select(Lease)
        .where(Lease.tenant_id == lease.tenant_id)
        .order_by(Lease.start_date, Lease.id)
    ).all()
    return [
        item
        for item in leases
        if item.id not in ignored and IGNORE_LEASE_MARK not in (item.notes or "")
    ]


def tenant_debt_lease_ids(
    session: Session,
    lease: Lease,
    ignored_lease_ids: Iterable[int] = (),
) -> list[int]:
    return [item.id for item in tenant_debt_leases(session, lease, ignored_lease_ids)]
