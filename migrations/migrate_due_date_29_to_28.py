"""
Migration: Copy rent_charges payment data from due_date=29th to due_date=28th.

Background
----------
After changing the payment due day from 29 to 28 on existing leases, the
charge-generation routine created new empty records on the 28th while the
original records (with real payment data) remained on the 29th.

This script finds every such pair — same lease_id, same billing period,
one record on the 28th and one on the 29th — and copies the payment fields
(personal_due, ip_paid, personal_paid, status) from the 29th record into
the 28th record.  After the copy the 29th record is deleted.

Idempotency
-----------
The script is safe to run multiple times.  Before copying it checks whether
the 28th record already has non-zero payment data; if it does the pair is
skipped.  Pairs where the 29th record has no payment data at all are also
skipped (nothing to migrate).

Usage
-----
    # dry-run (default) — prints what would change, touches nothing
    python migrations/migrate_due_date_29_to_28.py

    # apply changes
    python migrations/migrate_due_date_29_to_28.py --apply

    # apply and keep the 29th records instead of deleting them
    python migrations/migrate_due_date_29_to_28.py --apply --keep-old
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sqlalchemy import select

from rental_manager.database import SessionLocal, init_db
from rental_manager.models import RentCharge
from rental_manager.services.billing import update_rent_charge_status


def find_migration_pairs(session) -> list[tuple[RentCharge, RentCharge]]:
    """Return (record_28, record_29) pairs that need migration.

    A pair qualifies when:
    - Both records share the same lease_id and period_start.
    - record_28.due_date day == 28 and record_29.due_date day == 29.
    - record_29 has at least one non-zero payment field (something to copy).
    - record_28 has all payment fields at zero (not yet migrated).
    """
    # Fetch all records whose due_date falls on the 28th or 29th.
    charges_28 = session.scalars(
        select(RentCharge).where(
            RentCharge.due_date.cast(str).like("%-28")
        )
    ).all()
    charges_29 = session.scalars(
        select(RentCharge).where(
            RentCharge.due_date.cast(str).like("%-29")
        )
    ).all()

    # Index the 29th records by (lease_id, period_start) for fast lookup.
    index_29: dict[tuple[int, object], RentCharge] = {
        (c.lease_id, c.period_start): c for c in charges_29
    }

    pairs: list[tuple[RentCharge, RentCharge]] = []
    for c28 in charges_28:
        key = (c28.lease_id, c28.period_start)
        c29 = index_29.get(key)
        if c29 is None:
            continue  # No matching 29th record for this lease/period.

        # Skip if the 29th record carries no payment data.
        has_data_on_29 = (
            float(c29.personal_due or 0) != 0
            or float(c29.ip_paid or 0) != 0
            or float(c29.personal_paid or 0) != 0
            or c29.status not in ("pending", "")
        )
        if not has_data_on_29:
            continue

        # Skip if the 28th record already has payment data (already migrated).
        already_migrated = (
            float(c28.ip_paid or 0) != 0
            or float(c28.personal_paid or 0) != 0
            or c28.status not in ("pending", "")
        )
        if already_migrated:
            continue

        pairs.append((c28, c29))

    return pairs


def run_migration(apply: bool, keep_old: bool) -> None:
    init_db()

    with SessionLocal() as session:
        pairs = find_migration_pairs(session)

        if not pairs:
            print("No migration pairs found — nothing to do.")
            return

        print(f"Found {len(pairs)} pair(s) to migrate:\n")

        for c28, c29 in pairs:
            print(
                f"  lease_id={c28.lease_id}  period_start={c28.period_start}"
                f"\n    28th record (id={c28.id}): "
                f"personal_due={c28.personal_due}, ip_paid={c28.ip_paid}, "
                f"personal_paid={c28.personal_paid}, status={c28.status!r}"
                f"\n    29th record (id={c29.id}): "
                f"personal_due={c29.personal_due}, ip_paid={c29.ip_paid}, "
                f"personal_paid={c29.personal_paid}, status={c29.status!r}"
            )

            if apply:
                # Copy payment fields from the 29th to the 28th record.
                c28.personal_due = c29.personal_due
                c28.ip_paid = c29.ip_paid
                c28.personal_paid = c29.personal_paid

                # Recompute status from the freshly copied values so it is
                # consistent with the billing logic rather than blindly copying
                # the old status string.
                update_rent_charge_status(c28)

                print(
                    f"    → Copied to 28th record. New status: {c28.status!r}"
                )

                if not keep_old:
                    session.delete(c29)
                    print(f"    → Deleted 29th record (id={c29.id}).")
                else:
                    print(f"    → Kept 29th record (id={c29.id}) as-is.")

            print()

        if apply:
            session.commit()
            print(
                f"Migration complete. {len(pairs)} pair(s) processed"
                + (" (29th records deleted)." if not keep_old else " (29th records kept).")
            )
        else:
            print(
                f"Dry-run complete. {len(pairs)} pair(s) would be migrated. "
                "Pass --apply to execute."
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Migrate rent_charges payment data from due_date=29th to due_date=28th. "
            "Runs as a dry-run by default; pass --apply to commit changes."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit the migration to the database (default: dry-run).",
    )
    parser.add_argument(
        "--keep-old",
        action="store_true",
        help=(
            "Keep the 29th records after copying their data to the 28th records "
            "(default: delete them after migration)."
        ),
    )
    args = parser.parse_args()

    run_migration(apply=args.apply, keep_old=args.keep_old)


if __name__ == "__main__":
    main()
