from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from rental_manager.database import Base
from rental_manager.main import build_all_debts_breakdown, build_dashboard, render_message_text
from rental_manager.models import AppSetting, Apartment, Lease, Meter, MeterReading, RentalObject, RentCharge, Tenant, UtilityBill, UtilityBillLine, UtilityService
from rental_manager.services.billing import (
    LEGACY_IMPORT_MARK,
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

    def test_inactive_apartment_does_not_generate_new_rent_charges(self) -> None:
        with self.seed() as session:
            apartment = session.get(Apartment, 1)
            apartment.active = False
            tenant = Tenant(full_name="Paused tenant")
            session.add(tenant)
            session.flush()
            lease = Lease(
                apartment_id=apartment.id,
                tenant_id=tenant.id,
                start_date=date(2026, 4, 14),
                payment_day=14,
                ip_amount=20000,
                personal_amount=5000,
            )
            session.add(lease)
            session.flush()

            created = generate_rent_charges(session, until=date(2026, 6, 30))

        self.assertEqual(created, 0)

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

    def test_legacy_imported_rent_before_cutoff_does_not_require_phone_transfer(self) -> None:
        tenant = Tenant(full_name="Legacy Tenant", notes=LEGACY_IMPORT_MARK)
        lease = Lease(tenant=tenant, notes=LEGACY_IMPORT_MARK)
        charge = self._charge(due_date=date(2026, 3, 14), ip_due=20000, personal_due=5000)
        charge.lease = lease
        charge.ip_paid = 20000

        self.assertEqual(update_rent_charge_status(charge, today=date(2026, 4, 1)), "paid")
        self.assertEqual(charge.personal_paid, 5000)

    def test_current_rent_after_cutoff_still_requires_phone_transfer(self) -> None:
        tenant = Tenant(full_name="Fresh Tenant", notes=LEGACY_IMPORT_MARK)
        lease = Lease(tenant=tenant, notes=LEGACY_IMPORT_MARK)
        charge = self._charge(due_date=date(2026, 4, 14), ip_due=20000, personal_due=5000)
        charge.lease = lease
        charge.ip_paid = 20000

        self.assertEqual(update_rent_charge_status(charge, today=date(2026, 4, 15)), "partial")
        self.assertEqual(float(charge.personal_paid or 0), 0.0)

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

    def test_utility_bill_is_split_when_tenant_changes_inside_period(self) -> None:
        with self.seed() as session:
            obj = session.get(RentalObject, 1)
            apartment = session.get(Apartment, 1)
            service = session.scalar(select(UtilityService).where(UtilityService.object_id == obj.id, UtilityService.kind == "electricity"))
            self._lease(session, apartment, end_date=date(2026, 4, 3), full_name="First tenant")
            self._lease(session, apartment, start_date=date(2026, 4, 4), full_name="Second tenant")
            self._read_all_meters(session, service, object_start=1000, object_end=1310, apartment_end_values={apartment.id: 40})

            bill, warnings = calculate_utility_bill(session, service.id, date(2026, 4, 1), date(2026, 5, 1), allow_estimate=False)

        self.assertTrue(any("04.04.2026" in warning for warning in warnings))
        self.assertEqual(len(bill.lines), 2)
        self.assertEqual([line.note for line in bill.lines], ["01.04.2026 -> 04.04.2026 (3 дн.)", "04.04.2026 -> 01.05.2026 (27 дн.)"])
        self.assertAlmostEqual(sum(line.personal_consumption for line in bill.lines), 30.0)
        self.assertAlmostEqual(sum(line.odn_consumption for line in bill.lines), 280.0)

    def test_utility_bill_skips_interval_that_was_already_issued(self) -> None:
        with self.seed() as session:
            obj = session.get(RentalObject, 1)
            apartment = session.get(Apartment, 1)
            service = session.scalar(select(UtilityService).where(UtilityService.object_id == obj.id, UtilityService.kind == "electricity"))
            lease = self._lease(session, apartment)
            self._read_all_meters(session, service, object_start=1000, object_end=1310, apartment_end_values={apartment.id: 40})

            issued_bill = UtilityBill(
                service_id=service.id,
                period_start=date(2026, 4, 1),
                period_end=date(2026, 4, 10),
                status="issued",
                total_consumption=93,
                apartment_consumption=9,
                odn_consumption=84,
                total_cost=388.74,
                average_unit_price=4.18,
            )
            issued_bill.lines.append(
                UtilityBillLine(
                    apartment_id=apartment.id,
                    lease_id=lease.id,
                    personal_consumption=9,
                    odn_consumption=84,
                    total_amount=388.74,
                    paid_amount=0,
                    status="issued",
                    note="01.04.2026 -> 10.04.2026 (9 дн.)",
                )
            )
            session.add(issued_bill)
            session.flush()

            bill, warnings = calculate_utility_bill(session, service.id, date(2026, 4, 1), date(2026, 5, 1), allow_estimate=False)

        self.assertEqual(warnings[-1], "Пропущено уже выставленных сегментов: 1.")
        self.assertEqual(len(bill.lines), 1)
        self.assertEqual(bill.lines[0].note, "10.04.2026 -> 01.05.2026 (21 дн.)")

    def test_all_debts_message_groups_rent_and_utility_by_month(self) -> None:
        with self.seed() as session:
            obj = session.get(RentalObject, 1)
            apartment = session.get(Apartment, 1)
            service = session.scalar(select(UtilityService).where(UtilityService.object_id == obj.id, UtilityService.kind == "electricity"))
            lease = self._lease(session, apartment)
            from rental_manager.models import RentCharge

            feb_charge = RentCharge(
                lease_id=lease.id,
                period_start=date(2026, 2, 1),
                period_end=date(2026, 2, 28),
                due_date=date(2026, 2, 14),
                ip_due=20000,
                personal_due=2200,
                ip_paid=20000,
                personal_paid=0,
                status="partial",
            )
            mar_charge = RentCharge(
                lease_id=lease.id,
                period_start=date(2026, 3, 1),
                period_end=date(2026, 3, 31),
                due_date=date(2026, 3, 14),
                ip_due=20000,
                personal_due=2200,
                ip_paid=0,
                personal_paid=2200,
                status="partial",
            )
            feb_bill = UtilityBill(
                service_id=service.id,
                period_start=date(2026, 1, 15),
                period_end=date(2026, 2, 15),
                status="issued",
                total_consumption=0,
                apartment_consumption=0,
                odn_consumption=0,
                total_cost=14535,
                average_unit_price=0,
            )
            feb_bill.lines.append(
                UtilityBillLine(
                    apartment_id=apartment.id,
                    lease_id=lease.id,
                    total_amount=14535,
                    paid_amount=0,
                    status="overdue",
                    due_date=date(2026, 2, 20),
                    note="15.01.2026 -> 15.02.2026 (31 дн.)",
                )
            )
            mar_bill = UtilityBill(
                service_id=service.id,
                period_start=date(2026, 2, 15),
                period_end=date(2026, 3, 15),
                status="issued",
                total_consumption=0,
                apartment_consumption=0,
                odn_consumption=0,
                total_cost=11560,
                average_unit_price=0,
            )
            mar_bill.lines.append(
                UtilityBillLine(
                    apartment_id=apartment.id,
                    lease_id=lease.id,
                    total_amount=11560,
                    paid_amount=10000,
                    status="partial",
                    due_date=date(2026, 3, 20),
                    note="15.02.2026 -> 15.03.2026 (28 дн.)",
                )
            )
            session.add_all([feb_charge, mar_charge, feb_bill, mar_bill])
            session.flush()

            breakdown = build_all_debts_breakdown(session, lease, today=date(2026, 4, 24))
            text = render_message_text(session, "message_all_debts", lease)

        self.assertIn("Февраль:", breakdown)
        self.assertIn("ИП оплачено", breakdown)
        self.assertIn("перевод по номеру телефона не поступил", breakdown)
        self.assertIn("Март:", breakdown)
        self.assertIn("ИП не оплачено", breakdown)
        self.assertIn("Остаток", breakdown)
        self.assertIn("Отправьте чеки в этот чат в виде документа", text)

    def test_utility_message_lists_detailed_lines(self) -> None:
        with self.seed() as session:
            gas_service = session.scalar(select(UtilityService).where(UtilityService.kind == "gas"))
            water_service = session.scalar(select(UtilityService).where(UtilityService.kind == "water"))
            apartment = gas_service.object.apartments[0]
            lease = self._lease(session, apartment)

            gas_bill = UtilityBill(
                service_id=gas_service.id,
                period_start=date(2026, 3, 1),
                period_end=date(2026, 3, 31),
                status="issued",
                total_consumption=0,
                apartment_consumption=0,
                odn_consumption=0,
                total_cost=1200,
                average_unit_price=0,
                due_date=date(2026, 4, 24),
            )
            gas_line = UtilityBillLine(
                apartment_id=apartment.id,
                lease_id=lease.id,
                total_amount=1200,
                paid_amount=0,
                status="issued",
                due_date=date(2026, 4, 24),
                note="01.03.2026 -> 31.03.2026 (30 дн.)",
            )
            gas_bill.lines.append(gas_line)

            water_bill = UtilityBill(
                service_id=water_service.id,
                period_start=date(2026, 3, 1),
                period_end=date(2026, 3, 31),
                status="issued",
                total_consumption=0,
                apartment_consumption=0,
                odn_consumption=0,
                total_cost=800,
                average_unit_price=0,
                due_date=date(2026, 4, 24),
            )
            water_line = UtilityBillLine(
                apartment_id=apartment.id,
                lease_id=lease.id,
                total_amount=800,
                paid_amount=0,
                status="issued",
                due_date=date(2026, 4, 24),
                note="01.03.2026 -> 31.03.2026 (30 дн.)",
            )
            water_bill.lines.append(water_line)
            session.add_all([gas_bill, water_bill])
            session.flush()

            text = render_message_text(
                session,
                "message_utility_bill",
                lease,
                line=gas_line,
                utility_lines_override=[gas_line, water_line],
                utility_due_date_override=date(2026, 4, 24),
            )

        self.assertIn("Сформированы счета по коммунальным платежам", text)
        self.assertIn("газ за период 01.03.2026 -> 31.03.2026", text)
        self.assertIn("вода за период 01.03.2026 -> 31.03.2026", text)
        self.assertIn("Всего:", text)
        self.assertIn("Сбербанк", text)

    @staticmethod
    def _lease(session, apartment: Apartment, start_date: date = date(2026, 4, 1), end_date: date | None = None, full_name: str | None = None) -> Lease:
        tenant = Tenant(full_name=full_name or f"Tenant {apartment.id}")
        session.add(tenant)
        session.flush()
        lease = Lease(
            apartment_id=apartment.id,
            tenant_id=tenant.id,
            start_date=start_date,
            end_date=end_date,
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


class DashboardCutoffTests(DatabaseTestCase):
    def test_dashboard_ignores_debts_before_cutoff_date(self) -> None:
        with self.Session() as session:
            rental_object = RentalObject(name="Тестовый дом", short_code="ТД")
            tenant = Tenant(full_name="Старый хвост")
            session.add_all([rental_object, tenant, AppSetting(key="notification_cutoff_date", value="2026-01-01")])
            session.flush()

            apartment = Apartment(object_id=rental_object.id, name="ТД1", sort_order=1, odn_share_percent=100, active=True)
            session.add(apartment)
            session.flush()

            lease = Lease(
                apartment_id=apartment.id,
                tenant_id=tenant.id,
                start_date=date(2025, 1, 1),
                payment_day=14,
                ip_amount=20000,
                personal_amount=3000,
            )
            session.add(lease)
            session.flush()

            session.add_all(
                [
                    RentCharge(
                        lease_id=lease.id,
                        period_start=date(2025, 12, 14),
                        period_end=date(2026, 1, 13),
                        due_date=date(2025, 12, 14),
                        ip_due=20000,
                        personal_due=3000,
                        status="overdue",
                    ),
                    RentCharge(
                        lease_id=lease.id,
                        period_start=date(2026, 1, 14),
                        period_end=date(2026, 2, 13),
                        due_date=date(2026, 1, 14),
                        ip_due=20000,
                        personal_due=3000,
                        status="overdue",
                    ),
                ]
            )
            session.commit()

            dashboard = build_dashboard(session)

        self.assertEqual(len(dashboard["rent_overdue"]), 1)
        self.assertEqual(dashboard["rent_overdue"][0]["due_date"], "2026-01-14")


class StatusHelperTests(unittest.TestCase):
    def test_amount_statuses(self) -> None:
        self.assertEqual(status_for_amount(100, 0), "pending")
        self.assertEqual(status_for_amount(100, 99), "partial")
        self.assertEqual(status_for_amount(100, 100), "paid")
        self.assertEqual(status_for_amount(100, 101), "paid_ahead")


if __name__ == "__main__":
    unittest.main()
