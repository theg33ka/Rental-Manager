import unittest
from datetime import date

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from rental_manager.database import Base
from rental_manager.main import delete_lease, transfer_lease
from rental_manager.models import Apartment, Lease, RentalObject, RentCharge, Tenant


class RepeatTransferTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, autoflush=False)
        obj = RentalObject(name="Дом")
        self.source = Apartment(object=obj, name="БД3", active=True)
        self.target = Apartment(object=obj, name="ЧД3", active=True)
        self.lease = Lease(apartment=self.source, tenant=Tenant(full_name="Жилец"),
                           start_date=date(2026, 1, 1), payment_day=1, ip_amount=10000, active=True)
        self.session.add_all([self.lease, self.target])
        self.session.commit()
        result = transfer_lease(self.lease.id, {"apartment_id": self.target.id, "transfer_date": "2026-04-10"}, self.session)
        self.successor_id = result["new_lease"]["id"]

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def repeat(self, transfer_date="2026-05-10", **extra):
        return transfer_lease(self.lease.id, {
            "apartment_id": self.target.id, "transfer_date": transfer_date,
            "repeat_closed_transfer": True, **extra,
        }, self.session)

    def test_deleted_destination_can_be_recreated_without_reactivating_source(self):
        delete_lease(self.successor_id, self.session)
        result = self.repeat()
        self.assertFalse(result["old_lease"]["active"])
        self.assertEqual(result["old_lease"]["end_date"], "2026-05-09")
        self.assertEqual(result["new_lease"]["start_date"], "2026-05-10")
        self.assertEqual(result["new_lease"]["tenant_id"], self.lease.tenant_id)
        self.assertIsNotNone(self.session.scalar(select(RentCharge).where(
            RentCharge.lease_id == self.lease.id, RentCharge.due_date == date(2026, 5, 1))))
        self.assertIsNone(self.session.scalar(select(RentCharge).where(
            RentCharge.lease_id == self.lease.id, RentCharge.due_date > date(2026, 5, 9))))

    def test_existing_successor_blocks_repeat(self):
        with self.assertRaises(HTTPException):
            self.repeat()
        self.assertEqual(self.lease.end_date, date(2026, 4, 9))

    def test_repeat_requires_explicit_flag(self):
        delete_lease(self.successor_id, self.session)
        with self.assertRaises(HTTPException):
            self.repeat(repeat_closed_transfer=False)

    def test_source_and_target_historical_conflicts_block_repeat(self):
        delete_lease(self.successor_id, self.session)
        for apartment in (self.source, self.target):
            with self.subTest(apartment=apartment.name):
                conflict = Lease(apartment=apartment, tenant=Tenant(full_name="Другой жилец"),
                                 start_date=date(2026, 4, 15), end_date=date(2026, 5, 15),
                                 payment_day=15, active=False)
                self.session.add(conflict)
                self.session.flush()
                with self.assertRaises(HTTPException):
                    self.repeat()
                self.assertEqual(self.lease.end_date, date(2026, 4, 9))
                self.session.rollback()
