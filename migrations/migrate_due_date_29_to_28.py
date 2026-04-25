"""
Migration: Copy rent_charges payment data from due_date=29th to due_date=28th,
then delete the old 29th records.

Default:
    Processes only lease_id=19.

Dry-run:
    python migrations/migrate_due_date_29_to_28.py

Apply:
    python migrations/migrate_due_date_29_to_28.py --apply
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sqlalchemy import extract, select

from rental_manager.database import SessionLocal, init_db
from rental_manager.models import RentCharge
from rental_manager.services.billing import update_rent_charge_status


PAYMENT_FIELDS = ("personal_due", "ip_paid", "personal_paid")


def as_date(value):
    if isinstance(value, datetime):
        return value.date()
    return value


def money(value) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def has_payment_data(charge: RentCharge) -> bool:
    return any(money(getattr(charge, field)) != 0 for field in PAYMENT_FIELDS)


def payment_values_equal(c28: RentCharge, c29: RentCharge) -> bool:
    return all(
        money(getattr(c28, field)) == money(getattr(c29, field))
        for field in PAYMENT_FIELDS
    )


def find_migration_pairs(session, lease_id: int | None):
    """
    Finds matching 28th/29th pairs.

    Match strategy:
    1. Same lease_id + c28.due_date = c29.due_date - 1 day
    2. Fallback: same lease_id + same period_start
    """

    filters_28 = [extract("day", RentCharge.due_date) == 28]
    filters_29 = [extract("day", RentCharge.due_date) == 29]

    if lease_id is not None:
        filters_28.append(RentCharge.lease_id == lease_id)
        filters_29.append(RentCharge.lease_id == lease_id)

    charges_28 = session.scalars(
        select(RentCharge).where(*filters_28)
    ).all()

    charges_29 = session.scalars(
        select(RentCharge).where(*filters_29)
    ).all()

    index_28_by_due_date = {}
    index_28_by_period_start = {}

    for c28 in charges_28:
        index_28_by_due_date.setdefault(
            (c28.lease_id, as_date(c28.due_date)),
            c28,
        )

        index_28_by_period_start.setdefault(
            (c28.lease_id, c28.period_start),
            c28,
        )

    pairs = []
    used_28_ids = set()

    for c29 in charges_29:
        c28 = None
        reason = None

        c29_due_date = as_date(c29.due_date)
        target_28_due_date = c29_due_date - timedelta(days=1)

        c28 = index_28_by_due_date.get((c29.lease_id, target_28_due_date))
        if c28 is not None:
            reason = "matched by due_date - 1 day"

        if c28 is None:
            c28 = index_28_by_period_start.get((c29.lease_id, c29.period_start))
            if c28 is not None:
                reason = "matched by same period_start"

        if c28 is None:
            continue

        if c28.id in used_28_ids:
            continue

        used_28_ids.add(c28.id)
        pairs.append((c28, c29, reason))

    return pairs


def run_migration(apply: bool, lease_id: int | None, force_delete_conflicts: bool) -> None:
    init_db()

    with SessionLocal() as session:
        pairs = find_migration_pairs(session, lease_id=lease_id)

        if not pairs:
            print("No migration pairs found — nothing to do.")
            return

        print(f"Found {len(pairs)} pair(s) to process:\n")

        copied_count = 0
        deleted_count = 0
        skipped_conflicts_count = 0

        try:
            for c28, c29, reason in pairs:
                c28_has_data = has_payment_data(c28)
                c29_has_data = has_payment_data(c29)

                has_conflict = (
                    c28_has_data
                    and c29_has_data
                    and not payment_values_equal(c28, c29)
                )

                print(
                    f"lease_id={c28.lease_id}"
                    f"\n  match: {reason}"
                    f"\n  28th record id={c28.id}: "
                    f"period={c28.period_start} -> {c28.period_end}, "
                    f"due_date={c28.due_date}, "
                    f"personal_due={c28.personal_due}, "
                    f"ip_paid={c28.ip_paid}, "
                    f"personal_paid={c28.personal_paid}, "
                    f"status={c28.status!r}"
                    f"\n  29th record id={c29.id}: "
                    f"period={c29.period_start} -> {c29.period_end}, "
                    f"due_date={c29.due_date}, "
                    f"personal_due={c29.personal_due}, "
                    f"ip_paid={c29.ip_paid}, "
                    f"personal_paid={c29.personal_paid}, "
                    f"status={c29.status!r}"
                )

                if has_conflict and not force_delete_conflicts:
                    skipped_conflicts_count += 1
                    print(
                        "  WARNING: both records have different payment data. "
                        "Skipping this pair to avoid data loss."
                    )
                    print(
                        "  To delete the 29th record anyway, rerun with "
                        "--force-delete-conflicts."
                    )
                    print()
                    continue

                if apply:
                    if not c28_has_data and c29_has_data:
                        c28.personal_due = c29.personal_due
                        c28.ip_paid = c29.ip_paid
                        c28.personal_paid = c29.personal_paid

                        update_rent_charge_status(c28)

                        copied_count += 1
                        print(f"  → Copied data to 28th record. New status: {c28.status!r}")
                    else:
                        update_rent_charge_status(c28)
                        print(
                            "  → Copy skipped: 28th record already has data "
                            "or 29th record has no payment data."
                        )

                    session.delete(c29)
                    deleted_count += 1
                    print(f"  → Deleted 29th record id={c29.id}.")

                print()

            if apply:
                session.commit()
                print("Migration complete.")
                print(f"Copied records: {copied_count}")
                print(f"Deleted 29th records: {deleted_count}")
                print(f"Skipped conflicts: {skipped_conflicts_count}")
            else:
                print("Dry-run complete. No changes were made.")
                print("Run with --apply to copy data and delete 29th records.")

        except Exception:
            session.rollback()
            raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate rent_charges from due_date=29th to due_date=28th."
    )

    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit changes. Without this flag it is a dry-run.",
    )

    parser.add_argument(
        "--lease-id",
        type=int,
        default=19,
        help="Lease ID to process. Default: 19.",
    )

    parser.add_argument(
        "--all-leases",
        action="store_true",
        help="Process all leases instead of only one lease_id.",
    )

    parser.add_argument(
        "--force-delete-conflicts",
        action="store_true",
        help="Delete 29th records even if the 28th record has different payment data.",
    )

    args = parser.parse_args()

    lease_id = None if args.all_leases else args.lease_id

    run_migration(
        apply=args.apply,
        lease_id=lease_id,
        force_delete_conflicts=args.force_delete_conflicts,
    )


if __name__ == "__main__":
    main()