from datetime import date
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

from sqlalchemy import select, func

from rental_manager import main
from rental_manager.models import (AiActionLog, Apartment, Lease, PaymentReceipt, RentCharge,
                                  RentalObject, Tenant, UtilityBill, UtilityBillLine, UtilityService)
from tests.test_billing import DatabaseTestCase


class UtilityOverpaymentRepairTests(DatabaseTestCase):
    def migration(self):
        path = Path(__file__).resolve().parents[1] / "migrations/versions/20261028_02_utility_overpayment.py"
        spec = importlib.util.spec_from_file_location("overpayment_repair", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def fixture(self, session):
        house = RentalObject(name="Test house")
        apartment = Apartment(id=3, object=house, name="A3")
        lease = Lease(id=37, apartment=apartment, tenant=Tenant(id=32, full_name="Tenant"),
                      start_date=date(2026, 8, 25), payment_day=25, ip_amount=20000, personal_amount=5000)
        service = UtilityService(object=house, name="Electricity", kind="electricity")
        charge = RentCharge(id=986, lease=lease, period_start=date(2026, 10, 25), period_end=date(2026, 11, 24),
                            due_date=date(2026, 10, 25), ip_due=20000, personal_due=5000,
                            ip_paid=20000, personal_paid=2049, status="partial")
        bills = [UtilityBill(service=service, period_start=start, period_end=end, status=status)
                 for start, end, status in [(date(2026, 8, 25), date(2026, 9, 17), "issued"),
                                             (date(2026, 9, 17), date(2026, 10, 28), "draft")]]
        for bill, row_id, amount in zip(bills, [262, 280], [1047.05, 3660]):
            bill.lines.append(UtilityBillLine(id=row_id, apartment=apartment, lease=lease, total_amount=amount,
                                               paid_amount=2951, status="paid_ahead" if row_id == 262 else "partial"))
        session.add_all([house, apartment, lease, service, charge, *bills])
        session.flush()

        def receipt(row_id, amount, key, parsed_amount, status="accepted", channel="personal", rent=None, utility=None):
            return PaymentReceipt(id=row_id, apartment_id=3, lease_id=37, amount=amount, channel=channel,
                                  rent_charge_id=rent, utility_line_id=utility, source="telegram", status=status,
                                  recipient_details=json.dumps({"receipt_number": key, "amount": parsed_amount}),
                                  notes="Test provenance")

        session.add_all([
            receipt(353, 2951, "september", 2951, status="ignored"),
            receipt(354, 2951, "september", 2951, status="duplicate"),
            receipt(377, 5000, "october-rent", 5000, status="moderated", rent=986),
            receipt(379, 2049, "october-rent", 5000, rent=986),
            receipt(380, 2951, "october-rent", 5000, channel="utilities", utility=262),
            receipt(376, 2951, "october-utilities", 2951, channel="utilities", utility=280),
            receipt(378, 20000, "october-ip", 20000, channel="ip", rent=986),
        ])
        session.commit()

    def test_upgrade_restores_rent_pays_utilities_and_preserves_exact_remainder_once(self):
        with self.Session() as session:
            self.fixture(session)
        env = {**os.environ, "RENTAL_MANAGER_DATABASE_URL": str(self.engine.url)}
        for command in [("stamp", "20261028_01"), ("upgrade", "head"), ("upgrade", "head")]:
            result = subprocess.run([sys.executable, "-m", "alembic", *command], env=env,
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
        with self.engine.begin() as connection:
            self.migration().repair(connection)
        with self.Session() as session:
            self.assertEqual(session.get(RentCharge, 986).personal_paid, 5000)
            self.assertEqual(session.get(RentCharge, 986).status, "paid")
            self.assertEqual(session.get(UtilityBillLine, 262).paid_amount, 1047.05)
            self.assertEqual(session.get(UtilityBillLine, 280).paid_amount, 3660)
            self.assertEqual(session.get(UtilityBillLine, 280).bill.status, "draft")
            self.assertEqual(main.utility_advance_balance(session, 3, 37), 1194.95)
            self.assertEqual(session.get(PaymentReceipt, 354).status, "duplicate")
            self.assertEqual(session.get(PaymentReceipt, 353).status, "moderated")
            self.assertEqual(session.get(PaymentReceipt, 353).amount, 2951)
            self.assertEqual(session.get(PaymentReceipt, 380).amount, 2951)
            self.assertEqual(session.get(PaymentReceipt, 380).rent_charge_id, 986)
            total = session.scalar(select(func.sum(PaymentReceipt.amount)).where(PaymentReceipt.status == "accepted"))
            self.assertAlmostEqual(total, 30902)
            self.assertEqual(session.scalar(select(func.count()).select_from(AiActionLog)), 1)

    def test_receipt_identity_collision_refuses_repair_without_partial_changes(self):
        with self.Session() as session:
            self.fixture(session)
            session.get(PaymentReceipt, 376).recipient_details = session.get(PaymentReceipt, 353).recipient_details
            session.commit()
        with self.assertRaises(RuntimeError), self.engine.begin() as connection:
            self.migration().repair(connection)
        with self.Session() as session:
            self.assertEqual(session.get(PaymentReceipt, 353).status, "ignored")
            self.assertEqual(session.get(PaymentReceipt, 380).utility_line_id, 262)
            self.assertEqual(session.scalar(select(func.count()).select_from(AiActionLog)), 0)

    def test_new_payment_on_affected_invoice_requires_reconciliation_again(self):
        with self.Session() as session:
            self.fixture(session)
            session.add(PaymentReceipt(lease_id=37, apartment_id=3, utility_line_id=262, amount=1,
                                       channel="utilities", status="accepted"))
            session.commit()
        with self.assertRaises(RuntimeError), self.engine.begin() as connection:
            self.migration().repair(connection)

    def test_unrelated_database_is_unchanged(self):
        with self.engine.begin() as connection:
            self.migration().repair(connection)
