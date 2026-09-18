import unittest
from datetime import date

from fastapi import HTTPException
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session

from rental_manager.database import Base
from rental_manager.main import create_expense, credit_expense_rent, sync_rental_budget_expense_receipt
from rental_manager.models import Apartment, Expense, Lease, PaymentReceipt, RentalObject, RentCharge, Tenant


class ExpenseRentCreditTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, autoflush=False)
        self.apartment = Apartment(object=RentalObject(name="Баня"), name="Баня 3", active=True)
        self.lease = Lease(apartment=self.apartment, tenant=Tenant(full_name="Тест"),
                           start_date=date(2026, 7, 1), payment_day=6, ip_amount=10000,
                           personal_amount=5000, active=True)
        self.session.add(self.lease)
        self.session.commit()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def payload(self):
        return {"apartment_id": self.apartment.id, "expense_date": "2026-08-14", "amount": 11000,
                "source_funds": "rental_budget", "category": "Ремонт"}

    def assert_credit(self):
        charges = self.session.scalars(select(RentCharge).where(RentCharge.lease_id == self.lease.id)).all()
        august = next(c for c in charges if c.due_date == date(2026, 8, 6))
        september = next(c for c in charges if c.due_date == date(2026, 9, 6))
        self.assertEqual(august.ip_paid, 10000)
        self.assertEqual(september.ip_paid, 1000)
        self.assertTrue(all(c.personal_paid == 0 for c in charges))
        self.assertTrue(all(c.ip_paid == 0 for c in charges if c.due_date < date(2026, 8, 1)))
        self.assertEqual(self.session.scalar(select(func.count(Expense.id))), 1)

    def test_new_expense_credits_ip_and_carries_remainder_once(self):
        result = create_expense(self.payload(), self.session)
        self.assertEqual(result["rent_credit_amount"], 11000)
        self.assertEqual(credit_expense_rent(result["id"], self.session)["credited"], 0)
        for receipt in self.session.scalars(select(PaymentReceipt)).all():
            sync_rental_budget_expense_receipt(self.session, receipt)
        self.session.flush()
        self.assert_credit()

    def test_existing_host_style_expense_can_be_credited_without_duplicate_expense(self):
        expense = Expense(apartment_id=self.apartment.id, expense_date=date(2026, 8, 14),
                          amount=11000, source_funds="rental_budget")
        self.session.add(expense)
        self.session.commit()
        self.assertEqual(credit_expense_rent(expense.id, self.session)["credited"], 11000)
        self.assertEqual(credit_expense_rent(expense.id, self.session)["credited"], 0)
        self.assert_credit()

    def test_missing_apartment_rolls_back_expense(self):
        payload = self.payload()
        payload.pop("apartment_id")
        with self.assertRaises(HTTPException):
            create_expense(payload, self.session)
        self.assertEqual(self.session.scalar(select(func.count(Expense.id))), 0)

    def test_personal_expense_does_not_credit_rent(self):
        create_expense({**self.payload(), "source_funds": "personal"}, self.session)
        self.assertEqual(self.session.scalar(select(func.count(PaymentReceipt.id))), 0)
