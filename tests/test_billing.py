from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from rental_manager.database import Base
from rental_manager.models import Apartment, Lease, Meter, MeterReading, RentalObject, Tenant, UtilityService
from rental_manager.services.billing import (
    allocate_odn,
    calculate_tiered_cost,
    calculate_utility_bill,
    effective_due_date,
    full_months_lived,
    generate_rent_charges,
    next_due_after,
    reading_value_at,
    status_for_amount,
    update_rent_charge_status,
)
from rental_manager.services.seed import seed_if_empty


class DatabaseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        db_path = Path(self.tmp.name) / "test.db"
        self.engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, future=True)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.tmp.cleanup()

    def seed(self):
        session = self.Session()
        seed_if_empty(session)
        return session


class RentScheduleTests(DatabaseTestCase):
    def test_missing_payment_day_moves_to_first_of_next_month(self) -> None:
        self.assertEqual(effective_due_date(2026, 2, 29), date(2026, 3, 1))
        self.assertEqual(effective_due_date(2026, 2, 30), date(2026, 3, 1))
        self.assertEqual(effective_due_date(2026, 2, 31), date(2026, 3, 1))

    def test_next_due_returns_same_month_real_day_after_shifted_date(self) -> None:
        self.assertEqual(next_due_after(date(2026, 1, 31), 31), date(2026, 3, 1))
        self.assertEqual(next_due_after(date(2026, 3, 1), 31), date(2026, 3, 31))
        self.assertEqual(next_due_after(date(2026, 4, 30), 31), date(2026, 5, 1))
        self.assertEqual(next_due_after(date(2026, 5, 1), 31), date(2026, 5, 31))

    def test_generated_rent_charge_periods_for_31st_payment_day(self) -> None:
        with self.seed() as session:
            apartment = session.get(Apartment, 1)
            tenant = Tenant(full_name="Tenant")
            session.add(tenant)
            session.flush()
            lease = Lease(
                apartment_id=apartment.id,
                tenant_id=tenant.id,
                start_date=date(2026, 1, 31),
                payment_day=31,
                ip_amount=20000,
                personal_amount=5000,
            )
            session.add(lease)
            session.flush()

            created = generate_rent_charges(session, until=date(2026, 4, 5))
            charges = lease.rent_charges

        self.assertEqual(created, 3)
        self.assertEqual([charge.due_date for charge in charges], [date(2026, 1, 31), date(2026, 3, 1), date(2026, 3, 31)])
        self.assertEqual(charges[0].period_end, date(2026, 2, 28))
        self.assertEqual(charges[1].period_end, date(2026, 3, 30))

    def test_statuses_for_two_part_rent_payment(self) -> None:
        charge = self._charge(due_date=date(2026, 4, 14), ip_due=20000, personal_due=5000)
        self.assertEqual(update_rent_charge_status(charge, today=date(2026, 4, 14)), "pending")
        self.assertEqual(update_rent_charge_status(charge, today=date(2026, 4, 15)), "overdue")

        charge.ip_paid = 20000
        self.assertEqual(update_rent_charge_status(charge, today=date(2026, 4, 15)), "partial")

        charge.personal_paid = 5000
        self.assertEqual(update_rent_charge_status(charge, today=date(2026, 4, 15)), "paid")

        charge.personal_paid = 6000
        self.assertEqual(update_rent_charge_status(charge, today=date(2026, 4, 15)), "paid_ahead")

    def test_active_deferral_overrides_partial_debt(self) -> None:
        charge = self._charge(due_date=date(2026, 4, 14), ip_due=20000, personal_due=5000)
        charge.ip_paid = 20000
        charge.deferral_until = date(2026, 4, 25)

        self.assertEqual(update_rent_charge_status(charge, today=date(2026, 4, 23)), "deferred")

    def test_full_months_lived_uses_payment_periods(self) -> None:
        lease = Lease(start_date=date(2026, 4, 14), payment_day=14)
        self.assertEqual(full_months_lived(lease, date(2026, 5, 13)), 1)
        self.assertEqual(full_months_lived(lease, date(2026, 5, 14)), 1)
        self.assertEqual(full_months_lived(lease, date(2026, 6, 13)), 2)

    @staticmethod
    def _charge(**kwargs):
        from rental_manager.models import RentCharge

        defaults = {
            "lease_id": 1,
            "period_start": date(2026, 4, 14),
            "period_end": date(2026, 5, 13),
            "due_date": date(2026, 4, 14),
            "ip_due": 0,
            "personal_due": 0,
        }
        defaults.update(kwargs)
        return RentCharge(**defaults)


class UtilityBillingTests(DatabaseTestCase):
    def test_tiered_cost_is_applied_to_object_consumption(self) -> None:
        tiers = json.dumps(
            [
                {"limit": 3900, "price": 4.18},
                {"limit": 6000, "price": 6.01},
                {"limit": None, "price": 7.48},
            ]
        )
        self.assertEqual(calculate_tiered_cost(7000, tiers), 36403.0)

    def test_odn_is_reallocated_between_occupied_apartments_by_day(self) -> None:
        a1 = Apartment(id=1, odn_share_percent=20)
        a2 = Apartment(id=2, odn_share_percent=30)
        a3 = Apartment(id=3, odn_share_percent=50)
        leases = {
            1: Lease(start_date=date(2026, 4, 1), end_date=date(2026, 4, 1)),
            2: Lease(start_date=date(2026, 4, 1), end_date=None),
            3: None,
        }

        allocated = allocate_odn([a1, a2, a3], leases, date(2026, 4, 1), date(2026, 4, 3), 100)

        self.assertAlmostEqual(allocated[1], 20.0)
        self.assertAlmostEqual(allocated[2], 80.0)
        self.assertAlmostEqual(allocated[3], 0.0)

    def test_exact_utility_bill_uses_average_object_price(self) -> None:
        with self.seed() as session:
            obj = session.get(RentalObject, 1)
            apartment = session.get(Apartment, 1)
            service = session.scalar(select(UtilityService).where(UtilityService.object_id == obj.id, UtilityService.kind == "electricity"))
            self._lease(session, apartment)
            self._read_all_meters(session, service, object_start=1000, object_end=2200, apartment_end_values={apartment.id: 110})

            bill, warnings = calculate_utility_bill(session, service.id, date(2026, 4, 1), date(2026, 5, 1), allow_estimate=False)

        self.assertEqual(warnings, [])
        self.assertEqual(bill.total_consumption, 1200.0)
        self.assertEqual(bill.total_cost, 5016.0)
        self.assertEqual(bill.average_unit_price, 4.18)
        self.assertEqual(len(bill.lines), 1)
        self.assertEqual(bill.lines[0].personal_consumption, 100.0)
        self.assertEqual(bill.lines[0].odn_consumption, 1100.0)
        self.assertEqual(bill.lines[0].total_amount, 5016.0)

    def test_utility_bill_fails_when_apartment_consumption_exceeds_object(self) -> None:
        with self.seed() as session:
            obj = session.get(RentalObject, 1)
            apartment = session.get(Apartment, 1)
            service = session.scalar(select(UtilityService).where(UtilityService.object_id == obj.id, UtilityService.kind == "electricity"))
            self._lease(session, apartment)
            self._read_all_meters(session, service, object_start=1000, object_end=1050, apartment_end_values={apartment.id: 200})

            with self.assertRaisesRegex(ValueError, "квартирных"):
                calculate_utility_bill(session, service.id, date(2026, 4, 1), date(2026, 5, 1), allow_estimate=False)

    def test_reading_interpolation_and_forecast_are_marked_estimated(self) -> None:
        with self.seed() as session:
            meter = session.get(Meter, 1)
            session.add(MeterReading(meter_id=meter.id, reading_date=date(2026, 4, 1), value=1000))
            session.add(MeterReading(meter_id=meter.id, reading_date=date(2026, 4, 11), value=1100))
            session.add(MeterReading(meter_id=meter.id, reading_date=date(2026, 4, 21), value=1220))
            session.flush()

            interpolated = reading_value_at(session, meter.id, date(2026, 4, 16), allow_estimate=True)
            forecast = reading_value_at(session, meter.id, date(2026, 4, 25), allow_estimate=True)

        self.assertTrue(interpolated.estimated)
        self.assertEqual(interpolated.note, "interpolated")
        self.assertAlmostEqual(interpolated.value, 1160.0)
        self.assertTrue(forecast.estimated)
        self.assertEqual(forecast.note, "forecast")
        self.assertAlmostEqual(forecast.value, 1268.0)

    @staticmethod
    def _lease(session, apartment: Apartment) -> Lease:
        tenant = Tenant(full_name=f"Tenant {apartment.id}")
        session.add(tenant)
        session.flush()
        lease = Lease(
            apartment_id=apartment.id,
            tenant_id=tenant.id,
            start_date=date(2026, 4, 1),
            payment_day=1,
            ip_amount=10000,
            personal_amount=2000,
        )
        session.add(lease)
        session.flush()
        return lease

    @staticmethod
    def _read_all_meters(
        session,
        service: UtilityService,
        object_start: float,
        object_end: float,
        apartment_end_values: dict[int, float],
    ) -> None:
        meters = session.scalars(select(Meter).where(Meter.service_id == service.id)).all()
        for meter in meters:
            if meter.scope == "object":
                start_value = object_start
                end_value = object_end
            else:
                start_value = 10
                end_value = apartment_end_values.get(meter.apartment_id, 10)
            session.add(MeterReading(meter_id=meter.id, reading_date=date(2026, 4, 1), value=start_value))
            session.add(MeterReading(meter_id=meter.id, reading_date=date(2026, 5, 1), value=end_value))
        session.flush()


class StatusHelperTests(unittest.TestCase):
    def test_amount_statuses(self) -> None:
        self.assertEqual(status_for_amount(100, 0), "pending")
        self.assertEqual(status_for_amount(100, 99), "partial")
        self.assertEqual(status_for_amount(100, 100), "paid")
        self.assertEqual(status_for_amount(100, 101), "paid_ahead")


if __name__ == "__main__":
    unittest.main()
