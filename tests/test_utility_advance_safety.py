from datetime import date
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

from fastapi import HTTPException
from sqlalchemy import select, func

from rental_manager import main
from rental_manager.models import (AiActionLog, Apartment, Lease, ManualDebt, PaymentReceipt,
                                  RentalObject, Tenant, UtilityAdvanceLedger, UtilityBill, UtilityBillLine, UtilityService)
from rental_manager.services.utility_advances import monthly_history, usage_forecasts
from tests.test_billing import DatabaseTestCase


class UtilityAdvanceSafetyTests(DatabaseTestCase):
    def fixture(self, session):
        house = RentalObject(name="Test house")
        apartment = Apartment(id=3, object=house, name="A3", active=True)
        destination = Apartment(id=7, object=house, name="A7", active=True)
        old = Lease(id=29, apartment=apartment, tenant=Tenant(full_name="Previous"), start_date=date(2026, 5, 1), end_date=date(2026, 8, 7), payment_day=1, ip_amount=100, personal_amount=0)
        new = Lease(id=37, apartment=apartment, tenant=Tenant(full_name="New"), start_date=date(2026, 8, 25), payment_day=1, ip_amount=100, personal_amount=0)
        moved = Lease(id=41, apartment=destination, tenant=old.tenant, start_date=date(2026, 8, 8), payment_day=1, ip_amount=100, personal_amount=0)
        service = UtilityService(object=house, name="Electricity", kind="electricity")
        session.add_all([house, old, new, moved, service])
        session.flush()
        return apartment, destination, old, new, moved, service

    def usage(self, session, lease, service, start, end, amount, status="draft"):
        bill = UtilityBill(service=service, period_start=start, period_end=end, status=status)
        line = UtilityBillLine(apartment=lease.apartment, lease=lease, total_amount=amount, status=status)
        bill.lines.append(line)
        session.add(bill)
        session.flush()
        return bill, line

    def test_preview_and_issue_do_not_use_another_tenants_unlinked_or_ledger_advance(self):
        with self.Session() as session:
            apartment, _, old, new, _, service = self.fixture(session)
            session.add_all([PaymentReceipt(apartment_id=apartment.id, lease_id=old.id, amount=500, channel="utility_advance", status="accepted"),
                             UtilityAdvanceLedger(apartment_id=apartment.id, lease_id=old.id, amount=500, kind="advance_payment")])
            bill, line = self.usage(session, new, service, date(2026, 9, 1), date(2026, 10, 1), 70)
            session.flush()
            self.assertEqual(main.utility_advance_balance(session, apartment.id, new.id), 0)
            self.assertEqual(main.utility_advance_balance(session, apartment.id, old.id), 1000)
            self.assertEqual(main.serialize_bill_line(line, session)["advance_balance_available"], 0)
            self.assertEqual(main.projected_utility_debts_for_issue_preview(session, [line], [line])[line.id], 70)
            self.assertEqual(main.apply_utility_advances_to_bill(session, bill), 0)
            self.assertEqual(line.paid_amount, 0)

    def test_cancelled_and_multimonth_history_cannot_inflate_october_forecast(self):
        with self.Session() as session:
            _, _, old, new, _, service = self.fixture(session)
            old.start_date = date(2025, 1, 1)
            _, historic = self.usage(session, old, service, date(2025, 10, 2), date(2026, 5, 4), 86099.57, "issued")
            historic.metadata_json = json.dumps({"line_period_start": "2025-10-02", "line_period_end": "2025-11-02"})
            _, cancelled = self.usage(session, old, service, date(2025, 10, 1), date(2025, 11, 1), 99999, "cancelled")
            monthly, excluded = monthly_history([historic, cancelled], 31)
            self.assertEqual(monthly, {})
            self.assertEqual(excluded, 2)
            amount, metadata = main.estimated_apartment_utility_amount(session, new.apartment_id, date(2026, 10, 28), current_amount=2767.32)
            self.assertEqual(amount, 2767.32)
            self.assertEqual(metadata["history_months"], 0)

    def test_forecast_uses_occupied_days_and_sums_services_without_double_days(self):
        with self.Session() as session:
            _, _, _, lease, _, service = self.fixture(session)
            bill, _ = self.usage(session, lease, service, date(2026, 9, 17), date(2026, 10, 28), 3660)
            self.assertEqual(usage_forecasts([bill], 31)[lease.apartment_id], 2767.32)

    def migration(self):
        path = Path(__file__).resolve().parents[1] / "migrations/versions/20261028_01_utility_advance_transfer.py"
        spec = importlib.util.spec_from_file_location("transfer_repair", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def seed_repair(self, session):
        apartment, _, old, new, moved, service = self.fixture(session)
        bill, line = self.usage(session, new, service, date(2026, 8, 25), date(2026, 9, 17), 1047.05, "issued")
        line.id = 262
        line.paid_amount = 1047.05
        advance_bill = UtilityBill(service=service, period_start=date(2026, 6, 26), period_end=date(2026, 7, 26), bill_type="advance", status="issued")
        advance = UtilityBillLine(id=139, apartment=apartment, lease=old, line_type="advance", total_amount=2951.08, paid_amount=2951.08, status="paid")
        advance_bill.lines.append(advance)
        session.add(advance_bill)
        session.flush()
        session.add_all([
            PaymentReceipt(id=246, apartment_id=3, lease_id=29, utility_line_id=139, amount=2951.08, channel="utility_advance", status="accepted"),
            PaymentReceipt(id=350, apartment_id=7, lease_id=moved.id, amount=2951, channel="utility_advance", status="accepted", source="manual", notes="перенос аванса"),
            UtilityAdvanceLedger(id=3, apartment_id=3, lease_id=29, payment_receipt_id=246, amount=2951.08, kind="advance_payment"),
            UtilityAdvanceLedger(id=8, apartment_id=3, lease_id=37, utility_line_id=262, amount=-1047.05, kind="advance_applied"),
            ManualDebt(id=8, apartment_id=3, lease_id=37, kind="utility", title="Коррекция", amount=2951, paid_amount=0, active=True),
        ])
        session.commit()

    def test_repair_conserves_credit_replaces_compensation_and_is_idempotent(self):
        with self.Session() as session:
            self.seed_repair(session)
        env = {**os.environ, "RENTAL_MANAGER_DATABASE_URL": str(self.engine.url)}
        for command in [("stamp", "20261002_01"), ("upgrade", "head"), ("upgrade", "head")]:
            result = subprocess.run([sys.executable, "-m", "alembic", *command], env=env,
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
        with self.engine.begin() as connection:
            self.migration().repair(connection)
            self.migration().repair(connection)
        with self.Session() as session:
            self.assertFalse(session.get(ManualDebt, 8).active)
            self.assertEqual(session.get(UtilityBillLine, 262).paid_amount, 0)
            self.assertEqual(session.get(UtilityAdvanceLedger, 8).amount, 0)
            self.assertEqual(session.get(PaymentReceipt, 350).status, "transferred")
            self.assertEqual(main.utility_advance_balance(session, 3, 29), 0.08)
            self.assertEqual(main.utility_advance_balance(session, 7, 41), 2951)
            self.assertEqual(main.utility_advance_balance(session, 3, 37), 0)
            self.assertEqual(session.scalar(select(func.count()).select_from(AiActionLog)), 1)
            for operation in [main.update_payment_receipt, main.moderate_payment_receipt]:
                with self.assertRaises(HTTPException) as error:
                    operation(350, {}, session)
                self.assertEqual(error.exception.status_code, 409)

    def test_repair_refuses_changed_amount_and_rolls_back(self):
        with self.Session() as session:
            self.seed_repair(session)
            session.get(ManualDebt, 8).paid_amount = 1
            session.commit()
        with self.assertRaises(RuntimeError), self.engine.begin() as connection:
            self.migration().repair(connection)
        with self.Session() as session:
            self.assertTrue(session.get(ManualDebt, 8).active)
            self.assertEqual(session.get(UtilityAdvanceLedger, 8).amount, -1047.05)

    def test_repair_is_noop_on_unrelated_database(self):
        with self.engine.begin() as connection:
            self.migration().repair(connection)

    def test_autumn_and_winter_forecasts_have_bounded_growth(self):
        for month in [1, 4, 10, 12]:
            amount, metadata = main.capped_utility_advance_forecast(56000, 3000, date(2026, month, 1))
            self.assertEqual(amount, 6000)
            self.assertTrue(metadata["forecast_capped"])
            small_amount, _ = main.capped_utility_advance_forecast(5000, 70, date(2026, month, 1))
            self.assertEqual(small_amount, 140)

    def test_same_tenant_continuation_shares_balance_only_in_same_apartment(self):
        with self.Session() as session:
            apartment, _, old, _, moved, _ = self.fixture(session)
            continuation = Lease(apartment=apartment, tenant=old.tenant, start_date=date(2026, 8, 8),
                                 end_date=date(2026, 8, 24), payment_day=1, ip_amount=100, personal_amount=0)
            session.add(continuation)
            session.add(UtilityAdvanceLedger(apartment_id=3, lease_id=old.id, amount=500))
            session.flush()
            self.assertEqual(main.utility_advance_balance(session, 3, continuation.id), 500)
            self.assertEqual(main.utility_advance_balance(session, 7, moved.id), 0)

    def test_negative_adjustment_limits_spending_from_legacy_receipt(self):
        with self.Session() as session:
            apartment, _, _, tenant, _, service = self.fixture(session)
            session.add_all([PaymentReceipt(apartment_id=apartment.id, lease_id=tenant.id, amount=500,
                                           channel="utility_advance", status="accepted"),
                             UtilityAdvanceLedger(apartment_id=apartment.id, lease_id=tenant.id, amount=-200)])
            bill, line = self.usage(session, tenant, service, date(2026, 9, 1), date(2026, 10, 1), 1000)
            self.assertEqual(main.apply_utility_advances_to_bill(session, bill), 300)
            self.assertEqual(line.paid_amount, 300)
            self.assertEqual(main.utility_advance_balance(session, 3, tenant.id), 0)
