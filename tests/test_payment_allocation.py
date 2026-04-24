from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from rental_manager.database import Base
from rental_manager.models import Apartment, Lease, Tenant, UtilityBill, UtilityBillLine, UtilityService
from rental_manager.services.billing import generate_rent_charges
from rental_manager.services.payment_allocation import create_rent_receipts, create_utility_receipts
from rental_manager.services.seed import seed_if_empty


class PaymentAllocationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        db_path = Path(self.tmp.name) / "test.db"
        self.engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, future=True)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.tmp.cleanup()

    def test_rent_payment_goes_to_oldest_month_first(self) -> None:
        with self.Session() as session:
            seed_if_empty(session)
            apartment = session.get(Apartment, 1)
            tenant = Tenant(full_name="FIFO Tenant")
            session.add(tenant)
            session.flush()
            lease = Lease(
                apartment_id=apartment.id,
                tenant_id=tenant.id,
                start_date=date(2026, 1, 14),
                payment_day=14,
                ip_amount=20000,
                personal_amount=3000,
            )
            session.add(lease)
            session.flush()
            generate_rent_charges(session, until=date(2026, 3, 20))
            session.flush()

            create_rent_receipts(
                session,
                lease,
                "ip",
                20000,
                paid_at=datetime(2026, 3, 5, 12, 0),
                source="test",
                status="accepted",
            )
            session.commit()

            charges = sorted(lease.rent_charges, key=lambda item: item.due_date)
            self.assertEqual(charges[0].due_date, date(2026, 1, 14))
            self.assertEqual(charges[0].ip_paid, 20000.0)
            self.assertEqual(charges[1].ip_paid, 0.0)

    def test_telegram_rent_payment_prefers_same_month_when_it_is_still_empty(self) -> None:
        with self.Session() as session:
            seed_if_empty(session)
            apartment = session.get(Apartment, 1)
            tenant = Tenant(full_name="Month Match")
            session.add(tenant)
            session.flush()
            lease = Lease(
                apartment_id=apartment.id,
                tenant_id=tenant.id,
                start_date=date(2026, 1, 14),
                payment_day=14,
                ip_amount=20000,
                personal_amount=3000,
            )
            session.add(lease)
            session.flush()
            generate_rent_charges(session, until=date(2026, 4, 20))
            session.flush()

            create_rent_receipts(
                session,
                lease,
                "ip",
                20000,
                paid_at=datetime(2026, 4, 5, 12, 0),
                source="telegram",
                status="accepted",
                prefer_document_month=True,
            )
            session.commit()

            charges = sorted(lease.rent_charges, key=lambda item: item.due_date)
            january = next(charge for charge in charges if charge.due_date == date(2026, 1, 14))
            april = next(charge for charge in charges if charge.due_date == date(2026, 4, 14))
            self.assertEqual(january.ip_paid, 0.0)
            self.assertEqual(april.ip_paid, 20000.0)

    def test_telegram_rent_payment_falls_back_to_nearest_past_debt_after_same_month_was_already_touched(self) -> None:
        with self.Session() as session:
            seed_if_empty(session)
            apartment = session.get(Apartment, 1)
            tenant = Tenant(full_name="Month Fallback")
            session.add(tenant)
            session.flush()
            lease = Lease(
                apartment_id=apartment.id,
                tenant_id=tenant.id,
                start_date=date(2026, 1, 14),
                payment_day=14,
                ip_amount=20000,
                personal_amount=3000,
            )
            session.add(lease)
            session.flush()
            generate_rent_charges(session, until=date(2026, 4, 20))
            session.flush()

            create_rent_receipts(
                session,
                lease,
                "ip",
                5000,
                paid_at=datetime(2026, 4, 2, 12, 0),
                source="telegram",
                status="accepted",
                prefer_document_month=True,
            )
            create_rent_receipts(
                session,
                lease,
                "ip",
                20000,
                paid_at=datetime(2026, 4, 22, 12, 0),
                source="telegram",
                status="accepted",
                prefer_document_month=True,
            )
            session.commit()

            charges = sorted(lease.rent_charges, key=lambda item: item.due_date)
            march = next(charge for charge in charges if charge.due_date == date(2026, 3, 14))
            april = next(charge for charge in charges if charge.due_date == date(2026, 4, 14))
            self.assertEqual(april.ip_paid, 5000.0)
            self.assertEqual(march.ip_paid, 20000.0)

    def test_telegram_rent_payment_goes_to_next_month_as_advance_when_current_month_is_already_paid_and_no_old_debts(self) -> None:
        with self.Session() as session:
            seed_if_empty(session)
            apartment = session.get(Apartment, 1)
            tenant = Tenant(full_name="Advance Tenant")
            session.add(tenant)
            session.flush()
            lease = Lease(
                apartment_id=apartment.id,
                tenant_id=tenant.id,
                start_date=date(2026, 1, 14),
                payment_day=14,
                ip_amount=20000,
                personal_amount=3000,
            )
            session.add(lease)
            session.flush()
            generate_rent_charges(session, until=date(2026, 5, 20))
            session.flush()

            for paid_month in [1, 2, 3]:
                create_rent_receipts(
                    session,
                    lease,
                    "ip",
                    20000,
                    paid_at=datetime(2026, paid_month, 16, 12, 0),
                    source="seed",
                    status="accepted",
                    prefer_document_month=True,
                )
            create_rent_receipts(
                session,
                lease,
                "ip",
                20000,
                paid_at=datetime(2026, 4, 2, 12, 0),
                source="telegram",
                status="accepted",
                prefer_document_month=True,
            )
            create_rent_receipts(
                session,
                lease,
                "ip",
                20000,
                paid_at=datetime(2026, 4, 22, 12, 0),
                source="telegram",
                status="accepted",
                prefer_document_month=True,
            )
            session.commit()

            charges = sorted(lease.rent_charges, key=lambda item: item.due_date)
            april = next(charge for charge in charges if charge.due_date == date(2026, 4, 14))
            may = next(charge for charge in charges if charge.due_date == date(2026, 5, 14))
            self.assertEqual(april.ip_paid, 20000.0)
            self.assertEqual(may.ip_paid, 20000.0)

    def test_utility_payment_goes_to_oldest_bill_first(self) -> None:
        with self.Session() as session:
            seed_if_empty(session)
            apartment = session.get(Apartment, 1)
            service = session.scalar(select(UtilityService).where(UtilityService.object_id == apartment.object_id, UtilityService.kind == "electricity"))
            tenant = Tenant(full_name="Utility FIFO")
            session.add(tenant)
            session.flush()
            lease = Lease(
                apartment_id=apartment.id,
                tenant_id=tenant.id,
                start_date=date(2026, 1, 1),
                payment_day=1,
                ip_amount=10000,
                personal_amount=2000,
            )
            session.add(lease)
            session.flush()

            jan_bill = UtilityBill(service_id=service.id, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), status="issued")
            feb_bill = UtilityBill(service_id=service.id, period_start=date(2026, 2, 1), period_end=date(2026, 2, 28), status="issued")
            session.add_all([jan_bill, feb_bill])
            session.flush()
            jan_line = UtilityBillLine(bill_id=jan_bill.id, apartment_id=apartment.id, lease_id=lease.id, total_amount=3000, status="issued", due_date=date(2026, 2, 7))
            feb_line = UtilityBillLine(bill_id=feb_bill.id, apartment_id=apartment.id, lease_id=lease.id, total_amount=3000, status="issued", due_date=date(2026, 3, 7))
            session.add_all([jan_line, feb_line])
            session.flush()

            create_utility_receipts(
                session,
                lease,
                3000,
                paid_at=datetime(2026, 3, 10, 12, 0),
                source="test",
                status="accepted",
            )
            session.commit()

            self.assertEqual(jan_line.paid_amount, 3000.0)
            self.assertEqual(feb_line.paid_amount, 0.0)


if __name__ == "__main__":
    unittest.main()
