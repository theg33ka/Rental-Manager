from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from openpyxl import load_workbook
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import sessionmaker

from rental_manager.database import Base
from rental_manager.main import apartment_month_state, build_all_debts_breakdown, build_dashboard, create_move_out_utility_lines, expense_period_summary, owner_charge_status_label, owner_expected_ip_for_charge, panel_role_for_pin, process_move_out_notifications, provider_reading_statuses_for_month, render_message_text, rent_report, utility_bill_for_month
from rental_manager.main import apply_database_import_payload, current_database_snapshot, inspect_database_import_payload, parse_database_import_bytes
from rental_manager.models import AppSetting, Apartment, Expense, Lease, MessageLog, Meter, MeterReading, RentalObject, RentCharge, Tenant, UtilityBill, UtilityBillLine, UtilityService, Tariff
from rental_manager.services.billing import (
    IGNORE_LEASE_MARK,
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


class DatabaseImportExportTests(DatabaseTestCase):
    def test_database_snapshot_roundtrip_restores_records(self) -> None:
        with self.Session() as session:
            rental_object = RentalObject(name="Дом для экспорта", short_code="ЭКС")
            tenant = Tenant(full_name="Тестовый жилец")
            session.add_all([rental_object, tenant, AppSetting(key="telegram_owner_chat_id", value="123")])
            session.flush()
            apartment = Apartment(object_id=rental_object.id, name="ЭКС1", sort_order=1, odn_share_percent=100, active=True)
            session.add(apartment)
            session.flush()
            lease = Lease(apartment_id=apartment.id, tenant_id=tenant.id, start_date=date(2026, 5, 1), payment_day=1, ip_amount=20000, personal_amount=3000)
            session.add(lease)
            session.commit()

            snapshot = current_database_snapshot(session)
            inspection = inspect_database_import_payload(snapshot)

            session.execute(delete(Lease))
            session.execute(delete(Apartment))
            session.execute(delete(Tenant))
            session.execute(delete(RentalObject))
            session.execute(delete(AppSetting))
            session.commit()

            result = apply_database_import_payload(session, snapshot)
            session.commit()

            restored_lease = session.scalar(select(Lease).where(Lease.id == lease.id))
            restored_setting = session.get(AppSetting, "telegram_owner_chat_id")

        self.assertEqual(inspection["format"], "rental-manager-db-export")
        self.assertGreaterEqual(inspection["total_rows"], 5)
        self.assertEqual(result["counts"]["leases"], 1)
        self.assertIsNotNone(restored_lease)
        self.assertEqual(restored_setting.value, "123")

    def test_parse_database_import_bytes_rejects_wrong_payload(self) -> None:
        with self.assertRaises(HTTPException):
            parse_database_import_bytes(b'{"format":"nope","version":1,"tables":{}}')

    def test_panel_pin_defaults_resolve_owner_and_guest_roles(self) -> None:
        with self.Session() as session:
            self.assertEqual(panel_role_for_pin(session, "1298"), "owner")
            self.assertEqual(panel_role_for_pin(session, "1212"), "guest")
            self.assertIsNone(panel_role_for_pin(session, "0000"))


async def read_streaming_response_bytes(response) -> bytes:
    payload = b""
    async for chunk in response.body_iterator:
        payload += chunk
    return payload


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

    def test_total_rent_status_does_not_become_paid_ahead_without_real_overpayment(self) -> None:
        charge = self._charge(due_date=date(2026, 3, 2), period_end=date(2026, 4, 1), ip_due=20000, personal_due=0)
        charge.ip_paid = 20000

        self.assertEqual(update_rent_charge_status(charge, today=date(2026, 3, 16)), "paid")

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

    def test_owner_report_treats_expense_fund_payment_as_reduced_ip_expectation(self) -> None:
        from rental_manager.models import PaymentReceipt

        charge = self._charge(due_date=date(2026, 4, 14), ip_due=20000, personal_due=0)
        charge.receipts = [
            PaymentReceipt(amount=20000, channel="expense_fund", status="accepted", paid_at=date(2026, 4, 16)),
        ]
        charge.ip_paid = 20000

        self.assertEqual(owner_expected_ip_for_charge(charge), 0.0)
        self.assertEqual(owner_charge_status_label(charge), "оплачено, ушло на расходы")

    def test_owner_report_status_does_not_show_paid_ahead_without_overpayment(self) -> None:
        charge = self._charge(due_date=date(2026, 3, 2), period_end=date(2026, 4, 1), ip_due=20000, personal_due=0)
        charge.ip_paid = 20000

        self.assertEqual(owner_charge_status_label(charge), "оплачено")

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
    def test_expense_period_summary_keeps_opening_balance_and_sign(self) -> None:
        with self.seed() as session:
            session.add_all(
                [
                    Expense(expense_date=date(2025, 12, 20), amount=20000, source_funds="expense_fund_income", category="Пополнение"),
                    Expense(expense_date=date(2025, 12, 25), amount=11000, source_funds="personal", category="Ремонт"),
                    Expense(expense_date=date(2026, 1, 5), amount=3000, source_funds="personal", category="Кран"),
                    Expense(expense_date=date(2026, 1, 10), amount=2000, source_funds="expense_fund_income", category="Пополнение"),
                ]
            )
            session.commit()

            summary = expense_period_summary(session, date(2026, 1, 1), date(2026, 1, 31))

        self.assertEqual(summary["opening_balance"], -9000)
        self.assertEqual(summary["period_spent"], 3000)
        self.assertEqual(summary["period_income"], 2000)
        self.assertEqual(summary["closing_balance"], -8000)

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

    def test_move_out_utility_lines_are_created_as_forecasted_issued_bill(self) -> None:
        with self.Session() as session:
            rental_object = RentalObject(name="Тестовый дом", short_code="ТД")
            apartment = Apartment(name="ТД1", sort_order=1, odn_share_percent=100, active=True, object=rental_object)
            tenant = Tenant(full_name="Съезжающий жилец", active=True)
            lease = Lease(
                apartment=apartment,
                tenant=tenant,
                start_date=date(2026, 4, 1),
                end_date=date(2026, 4, 10),
                payment_day=1,
                ip_amount=10000,
                personal_amount=2000,
                active=False,
            )
            service = UtilityService(
                object=rental_object,
                kind="electricity",
                name="Электричество",
                provider_due_day=24,
                resident_due_days=7,
                active=True,
            )
            session.add_all([rental_object, apartment, tenant, lease, service])
            session.flush()
            session.add(Tariff(service_id=service.id, starts_on=date(2026, 1, 1), tiers_json=json.dumps([{"limit": None, "price": 4.18}])))
            object_meter = Meter(service_id=service.id, object_id=rental_object.id, scope="object", name="Общий", active=True)
            apartment_meter = Meter(service_id=service.id, object_id=rental_object.id, apartment_id=apartment.id, scope="apartment", name="ТД1", active=True)
            session.add_all([object_meter, apartment_meter])
            session.flush()
            session.add_all(
                [
                    MeterReading(meter_id=object_meter.id, reading_date=date(2026, 4, 1), value=1000),
                    MeterReading(meter_id=object_meter.id, reading_date=date(2026, 4, 20), value=1200),
                    MeterReading(meter_id=apartment_meter.id, reading_date=date(2026, 4, 1), value=10),
                    MeterReading(meter_id=apartment_meter.id, reading_date=date(2026, 4, 20), value=30),
                ]
            )
            session.commit()

            lines, warnings = create_move_out_utility_lines(session, lease)

        self.assertEqual(len(lines), 1)
        self.assertTrue(warnings)
        line = lines[0]
        self.assertEqual(line.status, "issued")
        self.assertEqual(line.bill.status, "issued")
        self.assertTrue(line.bill.is_forecast)
        self.assertTrue(line.bill.provider_paid)
        self.assertEqual(line.bill.period_start, date(2026, 4, 1))
        self.assertEqual(line.bill.period_end, date(2026, 4, 10))
        self.assertEqual(line.note, "01.04.2026 -> 10.04.2026 (9 дн.)")
        self.assertGreater(line.total_amount, 0)

    def test_move_out_utility_uses_last_issued_period_not_latest_draft(self) -> None:
        with self.Session() as session:
            rental_object = RentalObject(name="Draft test object", short_code="DTO")
            apartment = Apartment(name="DTO1", sort_order=1, odn_share_percent=100, active=True, object=rental_object)
            tenant = Tenant(full_name="Draft tenant", active=True)
            lease = Lease(
                apartment=apartment,
                tenant=tenant,
                start_date=date(2026, 4, 1),
                end_date=date(2026, 4, 10),
                payment_day=1,
                ip_amount=10000,
                personal_amount=2000,
                active=False,
            )
            service = UtilityService(object=rental_object, kind="electricity", name="Draft electricity", provider_due_day=24, resident_due_days=7, active=True)
            session.add_all([rental_object, apartment, tenant, lease, service])
            session.flush()
            session.add(Tariff(service_id=service.id, starts_on=date(2026, 1, 1), tiers_json=json.dumps([{"limit": None, "price": 4.18}])))
            object_meter = Meter(service_id=service.id, object_id=rental_object.id, scope="object", name="Object", active=True)
            apartment_meter = Meter(service_id=service.id, object_id=rental_object.id, apartment_id=apartment.id, scope="apartment", name="Apartment", active=True)
            session.add_all([object_meter, apartment_meter])
            session.flush()
            session.add_all(
                [
                    MeterReading(meter_id=object_meter.id, reading_date=date(2026, 4, 1), value=1000),
                    MeterReading(meter_id=object_meter.id, reading_date=date(2026, 4, 20), value=1200),
                    MeterReading(meter_id=apartment_meter.id, reading_date=date(2026, 4, 1), value=10),
                    MeterReading(meter_id=apartment_meter.id, reading_date=date(2026, 4, 20), value=30),
                ]
            )
            session.flush()

            issued_bill = UtilityBill(
                service_id=service.id,
                period_start=date(2026, 4, 1),
                period_end=date(2026, 4, 5),
                status="issued",
                due_date=date(2026, 4, 12),
                total_consumption=5,
                apartment_consumption=5,
                odn_consumption=0,
                total_cost=100,
                average_unit_price=4.18,
            )
            issued_bill.lines.append(
                UtilityBillLine(
                    apartment_id=apartment.id,
                    lease_id=lease.id,
                    personal_consumption=5,
                    odn_consumption=0,
                    total_amount=100,
                    paid_amount=0,
                    status="issued",
                    note="01.04.2026 -> 05.04.2026 (4 d.)",
                )
            )
            draft_bill = UtilityBill(
                service_id=service.id,
                period_start=date(2026, 4, 5),
                period_end=date(2026, 4, 8),
                status="draft",
                total_consumption=3,
                apartment_consumption=3,
                odn_consumption=0,
                total_cost=50,
                average_unit_price=4.18,
            )
            draft_bill.lines.append(
                UtilityBillLine(
                    apartment_id=apartment.id,
                    lease_id=lease.id,
                    personal_consumption=3,
                    odn_consumption=0,
                    total_amount=50,
                    paid_amount=0,
                    status="draft",
                    note="05.04.2026 -> 08.04.2026 (3 d.)",
                )
            )
            session.add_all([issued_bill, draft_bill])
            session.commit()

            lines, warnings = create_move_out_utility_lines(session, lease)

        self.assertEqual(len(lines), 1)
        self.assertTrue(warnings)
        self.assertEqual(lines[0].bill.period_start, date(2026, 4, 5))
        self.assertEqual(lines[0].bill.period_end, date(2026, 4, 10))

    def test_move_out_notifications_send_once_and_create_logs(self) -> None:
        with self.Session() as session:
            session.add_all(
                [
                    AppSetting(key="telegram_bot_token", value="token"),
                    AppSetting(key="telegram_owner_chat_id", value="999"),
                ]
            )
            rental_object = RentalObject(name="Тестовый дом 2", short_code="Т2")
            apartment = Apartment(name="Т21", sort_order=1, odn_share_percent=100, active=True, object=rental_object)
            tenant = Tenant(full_name="Жилец на выезд", active=True)
            lease = Lease(
                apartment=apartment,
                tenant=tenant,
                start_date=date(2026, 4, 1),
                end_date=date(2026, 4, 10),
                payment_day=1,
                ip_amount=10000,
                personal_amount=2000,
                active=False,
            )
            service = UtilityService(object=rental_object, kind="electricity", name="Электричество", provider_due_day=24, resident_due_days=7, active=True)
            session.add_all([rental_object, apartment, tenant, lease, service])
            session.flush()
            session.add(AppSetting(key="telegram_tenant_links", value=json.dumps({str(tenant.id): "123"})))
            session.add(Tariff(service_id=service.id, starts_on=date(2026, 1, 1), tiers_json=json.dumps([{"limit": None, "price": 4.18}])))
            object_meter = Meter(service_id=service.id, object_id=rental_object.id, scope="object", name="Общий", active=True)
            apartment_meter = Meter(service_id=service.id, object_id=rental_object.id, apartment_id=apartment.id, scope="apartment", name="Т21", active=True)
            session.add_all([object_meter, apartment_meter])
            session.flush()
            session.add_all(
                [
                    MeterReading(meter_id=object_meter.id, reading_date=date(2026, 4, 1), value=1000),
                    MeterReading(meter_id=object_meter.id, reading_date=date(2026, 4, 20), value=1200),
                    MeterReading(meter_id=apartment_meter.id, reading_date=date(2026, 4, 1), value=10),
                    MeterReading(meter_id=apartment_meter.id, reading_date=date(2026, 4, 20), value=30),
                ]
            )
            session.commit()

            with patch("rental_manager.main.send_telegram_text", return_value={"ok": True}) as mocked_send:
                summary = process_move_out_notifications(session, date(2026, 4, 10))
                session.commit()
                repeat = process_move_out_notifications(session, date(2026, 4, 10))

            self.assertEqual(summary["processed"], 1)
            self.assertEqual(summary["tenant_sent"], 1)
            self.assertEqual(summary["owner_sent"], 1)
            self.assertEqual(repeat["processed"], 0)
            self.assertEqual(mocked_send.call_count, 2)
            logs = session.scalars(select(MessageLog).where(MessageLog.lease_id == lease.id)).all()

        self.assertTrue(any(log.template_key == "message_utility_bill" for log in logs))

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
    def test_apartment_month_state_skips_ignored_lease(self) -> None:
        with self.Session() as session:
            rental_object = RentalObject(name="Дом-игнор", short_code="ДИ")
            apartment = Apartment(name="ДИ1", sort_order=1, odn_share_percent=100, active=True, object=rental_object)
            tenant = Tenant(full_name="Старая арендаторша")
            lease = Lease(
                apartment=apartment,
                tenant=tenant,
                start_date=date(2026, 4, 1),
                end_date=None,
                payment_day=1,
                ip_amount=20000,
                personal_amount=3000,
                notes=IGNORE_LEASE_MARK,
            )
            session.add_all([rental_object, apartment, tenant, lease])
            session.commit()

            state = apartment_month_state(apartment, date(2026, 4, 1), date(2026, 4, 30))

        self.assertEqual(state["tenant_names"], "нет жильца")
        self.assertTrue(state["vacant_full"])

    def test_rent_report_hides_ignored_lease_charge(self) -> None:
        with self.Session() as session:
            rental_object = RentalObject(name="Дом-отчёт", short_code="ДО")
            apartment = Apartment(name="ДО1", sort_order=1, odn_share_percent=100, active=True, object=rental_object)
            tenant = Tenant(full_name="Лишний хвост")
            lease = Lease(
                apartment=apartment,
                tenant=tenant,
                start_date=date(2026, 1, 1),
                end_date=None,
                payment_day=14,
                ip_amount=20000,
                personal_amount=3000,
                notes=IGNORE_LEASE_MARK,
            )
            charge = RentCharge(
                lease=lease,
                period_start=date(2026, 4, 14),
                period_end=date(2026, 5, 13),
                due_date=date(2026, 4, 14),
                ip_due=20000,
                personal_due=3000,
                status="overdue",
            )
            session.add_all([rental_object, apartment, tenant, lease, charge])
            session.commit()

            response = rent_report(start="2026-04-01", end="2026-04-30", session=session)
            workbook_bytes = asyncio.run(read_streaming_response_bytes(response))

        workbook = load_workbook(BytesIO(workbook_bytes))
        sheet = workbook["Аренда"]
        values = [cell for row in sheet.iter_rows(values_only=True) for cell in row if isinstance(cell, str)]
        self.assertNotIn("Лишний хвост", values)

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



class DashboardIssuedUtilityTests(DatabaseTestCase):
    def test_provider_reading_due_alert_starts_three_days_before_deadline(self) -> None:
        with self.seed() as session:
            service = session.scalar(select(UtilityService).where(UtilityService.kind == "electricity"))
            service.provider_reading_due_day = 20
            session.commit()

            statuses = provider_reading_statuses_for_month(session, 2026, 5, today=date(2026, 5, 17))
            target = next(item for item in statuses if item["service_id"] == service.id)
            self.assertEqual(target["status"], "pending")
            self.assertTrue(target["alert"])
            self.assertEqual(target["due_date"], "2026-05-20")

            meter = session.scalar(select(Meter).where(Meter.service_id == service.id, Meter.scope == "object"))
            session.add(MeterReading(meter_id=meter.id, reading_date=date(2026, 5, 18), value=1234))
            session.commit()

            statuses = provider_reading_statuses_for_month(session, 2026, 5, today=date(2026, 5, 19))
            target = next(item for item in statuses if item["service_id"] == service.id)
            self.assertEqual(target["status"], "paid")
            self.assertFalse(target["alert"])
            self.assertEqual(target["reading_date"], "2026-05-18")

    def test_utility_bill_month_is_based_on_period_start(self) -> None:
        with self.seed() as session:
            service = session.scalar(select(UtilityService).where(UtilityService.kind == "electricity"))
            bill = UtilityBill(
                service_id=service.id,
                period_start=date(2026, 4, 1),
                period_end=date(2026, 5, 1),
                status="issued",
                total_consumption=0,
                apartment_consumption=0,
                odn_consumption=0,
                total_cost=1000,
                average_unit_price=0,
                due_date=date(2026, 5, 8),
                provider_paid=True,
                provider_paid_at=datetime(2026, 5, 8, 12, 0),
            )
            session.add(bill)
            session.commit()

            april_start, april_end = date(2026, 4, 1), date(2026, 4, 30)
            may_start, may_end = date(2026, 5, 1), date(2026, 5, 31)

            self.assertEqual(utility_bill_for_month(session, service, april_start, april_end).id, bill.id)
            self.assertIsNone(utility_bill_for_month(session, service, may_start, may_end))

    def test_dashboard_includes_issued_utility_lines(self) -> None:
        with self.seed() as session:
            service = session.scalar(select(UtilityService).where(UtilityService.kind == "electricity"))
            apartment = service.object.apartments[0]
            tenant = Tenant(full_name="Жилец с выставленной коммуналкой")
            session.add(tenant)
            session.flush()

            lease = Lease(
                apartment_id=apartment.id,
                tenant_id=tenant.id,
                start_date=date(2026, 4, 1),
                payment_day=14,
                ip_amount=20000,
                personal_amount=3000,
            )
            session.add(lease)
            session.flush()

            bill = UtilityBill(
                service_id=service.id,
                period_start=date(2026, 4, 1),
                period_end=date(2026, 4, 20),
                status="issued",
                total_consumption=0,
                apartment_consumption=0,
                odn_consumption=0,
                total_cost=1800,
                average_unit_price=0,
                due_date=date(2026, 4, 27),
            )
            bill.lines.append(
                UtilityBillLine(
                    apartment_id=apartment.id,
                    lease_id=lease.id,
                    total_amount=1800,
                    paid_amount=0,
                    status="issued",
                    due_date=date(2026, 4, 27),
                    note="01.04.2026 -> 20.04.2026 (19 дн.)",
                )
            )
            session.add(bill)
            session.commit()

            dashboard = build_dashboard(session)

        self.assertEqual(len(dashboard["utility_issued"]), 1)
        self.assertEqual(dashboard["utility_issued"][0]["tenant"], "Жилец с выставленной коммуналкой")


class StatusHelperTests(unittest.TestCase):
    def test_amount_statuses(self) -> None:
        self.assertEqual(status_for_amount(100, 0), "pending")
        self.assertEqual(status_for_amount(100, 99), "partial")
        self.assertEqual(status_for_amount(100, 100), "paid")
        self.assertEqual(status_for_amount(100, 101), "paid_ahead")
        self.assertEqual(status_for_amount(0, 0), "paid")


if __name__ == "__main__":
    unittest.main()
