from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

from sqlalchemy import extract, select, update

from rental_manager.database import SessionLocal
from rental_manager.models import Lease, PaymentReceipt, RentCharge
from rental_manager.services.billing import generate_rent_charges


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("maintenance.rent_charges")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Проверка и исправление начислений аренды.")
    parser.add_argument("operation", choices=("delete-ids", "shift-payment-day"))
    parser.add_argument("--ids", nargs="*", type=int, default=[])
    parser.add_argument("--old-day", type=int)
    parser.add_argument("--new-day", type=int)
    parser.add_argument("--reason", default="")
    parser.add_argument("--apply", action="store_true", help="Применить изменения. Без флага выполняется dry-run.")
    return parser.parse_args()


def delete_ids(session, ids: list[int]) -> int:
    if not ids:
        raise ValueError("Для delete-ids передайте хотя бы один --ids.")
    charges = session.scalars(select(RentCharge).where(RentCharge.id.in_(sorted(set(ids))))).all()
    charge_ids = [charge.id for charge in charges]
    if charge_ids:
        session.execute(
            update(PaymentReceipt).where(PaymentReceipt.rent_charge_id.in_(charge_ids)).values(rent_charge_id=None)
        )
    for charge in charges:
        session.delete(charge)
    return len(charges)


def shift_payment_day(session, old_day: int | None, new_day: int | None) -> int:
    if not old_day or not new_day or not 1 <= old_day <= 31 or not 1 <= new_day <= 31:
        raise ValueError("Для shift-payment-day передайте корректные --old-day и --new-day.")
    leases = session.scalars(select(Lease).where(Lease.payment_day == old_day)).all()
    for lease in leases:
        charges = session.scalars(
            select(RentCharge).where(
                RentCharge.lease_id == lease.id,
                extract("day", RentCharge.due_date) == old_day,
            )
        ).all()
        charge_ids = [charge.id for charge in charges]
        if charge_ids:
            session.execute(
                update(PaymentReceipt).where(PaymentReceipt.rent_charge_id.in_(charge_ids)).values(rent_charge_id=None)
            )
        for charge in charges:
            session.delete(charge)
        lease.payment_day = new_day
    session.flush()
    generate_rent_charges(session, until=date.today() + timedelta(days=90))
    return len(leases)


def main() -> None:
    args = parse_args()
    if args.apply and not args.reason.strip():
        raise SystemExit("Для применения укажите --reason.")
    with SessionLocal() as session:
        try:
            if args.operation == "delete-ids":
                affected = delete_ids(session, args.ids)
            else:
                affected = shift_payment_day(session, args.old_day, args.new_day)
            logger.info(
                "operation=%s affected=%s mode=%s reason=%s",
                args.operation,
                affected,
                "apply" if args.apply else "dry-run",
                args.reason.strip() or "not-provided",
            )
            if args.apply:
                session.commit()
            else:
                session.rollback()
        except Exception:
            session.rollback()
            logger.exception("operation=%s failed", args.operation)
            raise


if __name__ == "__main__":
    main()
