import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from rental_manager.database import Base
from rental_manager.main import apply_utility_advances_to_bill, delete_utility_bill, utility_advance_balance
from rental_manager.models import AiActionLog, Apartment, Lease, MessageLog, PaymentReceipt, RentalObject, Tenant, UtilityAdvanceLedger, UtilityBill, UtilityBillLine, UtilityService


class UtilityDraftDeletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.tmp.name).as_posix()}/test.db")

        @event.listens_for(self.engine, "connect")
        def enforce_foreign_keys(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, autoflush=False)()
        obj = RentalObject(name="Дом", short_code="Д")
        apartment = Apartment(name="Д1", object=obj)
        tenant = Tenant(full_name="Жилец")
        lease = Lease(apartment=apartment, tenant=tenant, start_date=date(2026, 8, 25), payment_day=25, ip_amount=0, personal_amount=0)
        service = UtilityService(object=obj, name="Электричество", kind="electricity")
        self.bill = UtilityBill(service=service, period_start=date(2026, 9, 17), period_end=date(2026, 10, 28), status="draft")
        self.line = UtilityBillLine(apartment=apartment, lease=lease, total_amount=3660, paid_amount=3660, status="paid")
        self.bill.lines.append(self.line)
        self.session.add(self.bill)
        self.session.flush()
        self.receipt = PaymentReceipt(lease_id=lease.id, apartment_id=apartment.id, utility_line_id=self.line.id,
                                      amount=3660, channel="utilities", source="telegram", status="accepted",
                                      paid_at=datetime(2026, 10, 27), notes="Исходный чек", file_path="receipt.pdf")
        self.session.add(self.receipt)
        self.session.commit()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()
        self.tmp.cleanup()

    def test_paid_draft_returns_same_receipt_and_can_be_reapplied_once(self):
        bill_id = self.bill.id
        lease_id, apartment_id = self.line.lease_id, self.line.apartment_id
        self.session.add(MessageLog(lease_id=lease_id, utility_line_id=self.line.id, text="История"))
        self.session.commit()
        result = delete_utility_bill(bill_id, self.session)
        self.assertEqual(result["returned_to_advance"], 3660)
        self.assertEqual(self.receipt.amount, 3660)
        self.assertEqual(self.receipt.source, "telegram")
        self.assertEqual(self.receipt.file_path, "receipt.pdf")
        self.assertIsNone(self.receipt.utility_line_id)
        self.assertEqual(self.receipt.channel, "utility_advance")
        self.assertEqual(utility_advance_balance(self.session, apartment_id, lease_id), 3660)
        self.assertIsNone(self.session.scalar(select(MessageLog)).utility_line_id)
        audit = self.session.scalar(select(AiActionLog))
        self.assertEqual(json.loads(audit.payload_json)["receipt_ids"], [self.receipt.id])
        with self.assertRaises(HTTPException):
            delete_utility_bill(bill_id, self.session)
        new_bill = UtilityBill(service_id=self.bill.service_id, period_start=date(2026, 9, 17), period_end=date(2026, 10, 28), status="draft")
        new_bill.lines.append(UtilityBillLine(apartment_id=apartment_id, lease_id=lease_id, total_amount=3660))
        self.session.add(new_bill)
        self.session.flush()
        self.assertEqual(apply_utility_advances_to_bill(self.session, new_bill), 3660)
        self.assertEqual(apply_utility_advances_to_bill(self.session, new_bill), 0)
        self.assertEqual(utility_advance_balance(self.session, apartment_id, lease_id), 0)

    def test_ledger_application_is_returned_without_changing_original_amount(self):
        self.session.delete(self.receipt)
        credit = UtilityAdvanceLedger(apartment_id=self.line.apartment_id, lease_id=self.line.lease_id, amount=4000, kind="adjustment")
        applied = UtilityAdvanceLedger(apartment_id=self.line.apartment_id, lease_id=self.line.lease_id, utility_line_id=self.line.id, amount=-3660, kind="advance_applied")
        self.session.add_all([credit, applied])
        self.session.commit()
        lease_id, apartment_id = self.line.lease_id, self.line.apartment_id
        delete_utility_bill(self.bill.id, self.session)
        self.assertEqual(applied.amount, -3660)
        self.assertIsNone(applied.utility_line_id)
        self.assertEqual(utility_advance_balance(self.session, apartment_id, lease_id), 4000)

    def test_issued_payment_is_blocked_with_readable_message(self):
        self.bill.status = "issued"
        self.session.commit()
        with self.assertRaises(HTTPException) as ctx:
            delete_utility_bill(self.bill.id, self.session)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("оплаченный счёт", ctx.exception.detail)
        self.assertEqual(self.receipt.utility_line_id, self.line.id)

    def test_unexplained_paid_amount_is_blocked_without_mutation(self):
        self.line.paid_amount = 4000
        self.session.commit()
        with self.assertRaises(HTTPException) as ctx:
            delete_utility_bill(self.bill.id, self.session)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(self.receipt.channel, "utilities")
        self.assertEqual(self.receipt.utility_line_id, self.line.id)

    def test_other_tenant_receipt_is_blocked(self):
        self.receipt.lease_id = None
        self.session.commit()
        with self.assertRaises(HTTPException) as ctx:
            delete_utility_bill(self.bill.id, self.session)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_paid_advance_draft_is_blocked(self):
        self.bill.bill_type = "advance"
        self.session.commit()
        with self.assertRaises(HTTPException):
            delete_utility_bill(self.bill.id, self.session)
        self.assertEqual(self.receipt.utility_line_id, self.line.id)
