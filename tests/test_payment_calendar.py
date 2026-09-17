import asyncio
import json
from datetime import date
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from rental_manager import main
from rental_manager.models import Apartment, Lease, RentalObject, RentCharge, Tenant, UtilityBill, UtilityBillLine, UtilityService
from rental_manager.security.sessions import issue_session
from rental_manager.services.payment_calendar import payment_calendar
from tests.test_billing import DatabaseTestCase


class PaymentCalendarTests(DatabaseTestCase):
    def fixture(self, session):
        obj = RentalObject(name="Новый объект", short_code="НО")
        apartment = Apartment(object=obj, name="Студия А")
        lease = Lease(apartment=apartment, tenant=Tenant(full_name="Первый жилец"), start_date=date(2026, 6, 26), end_date=date(2026, 9, 7), active=False, payment_day=1)
        service = UtilityService(object=obj, name="Вода", kind="water", active=True)
        session.add_all([apartment, lease, service])
        session.flush()
        return obj, apartment, lease, service

    def line(self, session, apartment, lease, service, start, end, *, status="issued", paid=0, amount=100, kind="usage", due=date(2026, 9, 20), draft=False):
        bill = UtilityBill(service=service, period_start=date(2026, 6, 26), period_end=date(2026, 10, 1), status="draft" if draft else "issued", bill_type="advance" if kind == "advance" else "utility")
        line = UtilityBillLine(bill=bill, apartment=apartment, lease=lease, status=status, total_amount=amount, paid_amount=paid, due_date=due, line_type=kind, metadata_json=json.dumps({"line_period_start": start, "line_period_end": end}))
        session.add(bill)
        session.flush()
        return line

    def calendar(self, session, mode="utility", **kwargs):
        return payment_calendar(session, date(2026, 9, 1), date(2026, 9, 30), mode=mode, today=date(2026, 9, 17), **kwargs)

    def test_turnover_vacancy_and_new_objects_are_dynamic(self):
        with self.Session() as session:
            obj, apartment, old, service = self.fixture(session)
            new = Lease(apartment=apartment, tenant=Tenant(full_name="Дарья"), start_date=date(2026, 9, 15), payment_day=15)
            black = Apartment(object=RentalObject(name="Чёрный дом"), name="Номер 7")
            moved = Lease(apartment=black, tenant=old.tenant, start_date=date(2026, 9, 8), payment_day=1)
            session.add_all([new, moved])
            session.flush()
            self.line(session, apartment, old, service, "2026-06-26", "2026-09-08", paid=100)
            self.line(session, apartment, new, service, "2026-09-15", "2026-09-18")
            payload = self.calendar(session)
            row = payload["objects"][0]["apartments"][0]
            self.assertEqual([day["status"] for day in row["days"][:18]], ["paid"] * 7 + ["vacant"] * 7 + ["issued"] * 3 + ["unbilled"])
            self.assertEqual(row["days"][14]["lease_ids"], [new.id])
            self.assertTrue(row["days"][14]["move_in"])
            self.assertTrue(row["days"][6]["move_out"])
            self.assertEqual(payload["objects"][1]["apartments"][0]["days"][7]["lease_ids"], [moved.id])
            empty = RentalObject(name="Добавлен позже")
            session.add(empty)
            session.flush()
            self.assertEqual(self.calendar(session)["objects"][-1]["apartments"], [])
            session.add(Apartment(object=empty, name="Новая квартира"))
            session.flush()
            self.assertEqual(self.calendar(session)["objects"][-1]["apartments"][0]["days"][0]["status"], "vacant")

    def test_multiple_services_do_not_hide_debt_or_missing_calculation(self):
        with self.Session() as session:
            obj, apartment, lease, service = self.fixture(session)
            second = UtilityService(object=obj, name="Свет", kind="electricity", active=True)
            session.add(second)
            session.flush()
            self.line(session, apartment, lease, service, "2026-09-01", "2026-09-08", paid=100)
            row = self.calendar(session)["objects"][0]["apartments"][0]
            self.assertEqual(row["days"][0]["status"], "incomplete")
            self.assertEqual(row["days"][0]["missing_services"], ["Свет"])
            light = self.line(session, apartment, lease, second, "2026-09-01", "2026-09-08", paid=20)
            self.assertEqual(self.calendar(session)["objects"][0]["apartments"][0]["days"][0]["status"], "partial")
            light.due_date = date(2026, 9, 10)
            session.flush()
            self.assertEqual(self.calendar(session)["objects"][0]["apartments"][0]["days"][0]["status"], "overdue")

    def test_draft_and_paid_advance_are_not_paid_usage(self):
        with self.Session() as session:
            _, apartment, lease, service = self.fixture(session)
            self.line(session, apartment, lease, service, "2026-09-01", "2026-09-08", paid=100, kind="advance")
            row = self.calendar(session)["objects"][0]["apartments"][0]
            self.assertEqual(row["days"][0]["status"], "unbilled")
            self.assertEqual(len(row["days"][0]["entry_ids"]), 1)
            self.line(session, apartment, lease, service, "2026-09-01", "2026-09-08", paid=100, draft=True, status="paid")
            self.assertEqual(self.calendar(session)["objects"][0]["apartments"][0]["days"][0]["status"], "draft")

    def test_ignored_cancelled_archived_and_conflicting_contracts(self):
        with self.Session() as session:
            _, apartment, lease, service = self.fixture(session)
            apartment.active = False
            self.line(session, apartment, lease, service, "2026-09-01", "2026-09-08", status="cancelled")
            self.assertEqual(self.calendar(session)["objects"][0]["apartments"][0]["days"][0]["status"], "unbilled")
            ignored = self.calendar(session, ignored_lease_ids={lease.id})["objects"][0]["apartments"][0]
            self.assertEqual(ignored["days"][0]["status"], "vacant")
            self.assertFalse(ignored["active"])
            session.add(Lease(apartment=apartment, tenant=Tenant(full_name="Пересекающийся договор"), start_date=date(2026, 9, 2), payment_day=2))
            session.flush()
            self.assertEqual(self.calendar(session)["objects"][0]["apartments"][0]["days"][1]["status"], "conflict")

    def test_rent_uses_inclusive_end_and_separate_payment_parts_without_mutation(self):
        with self.Session() as session:
            _, apartment, lease, _ = self.fixture(session)
            charge = RentCharge(lease=lease, period_start=date(2026, 9, 1), period_end=date(2026, 9, 30), due_date=date(2026, 9, 20), ip_due=100, ip_paid=150, personal_due=50, personal_paid=0)
            session.add(charge)
            session.flush()
            session.expire_all()
            row = self.calendar(session, "rent")["objects"][0]["apartments"][0]
            self.assertEqual(row["days"][6]["status"], "partial")
            self.assertEqual(row["days"][7]["status"], "vacant")
            self.assertEqual(row["entries"][0]["debt"], 50)
            self.assertEqual(row["entries"][0]["period_end"], "2026-09-30")
            self.assertFalse(session.dirty)
            self.assertFalse(session.new)

    def test_period_boundaries_and_route_validation(self):
        with self.Session() as session:
            _, apartment, lease, service = self.fixture(session)
            self.line(session, apartment, lease, service, "2026-09-01", "2026-09-03", paid=100)
            row = self.calendar(session)["objects"][0]["apartments"][0]
            self.assertEqual(row["days"][1]["status"], "paid")
            self.assertEqual(row["days"][2]["status"], "unbilled")
            for start, end, mode in [(date(2026, 1, 1), date(2026, 12, 1), "utility"), (date(2026, 9, 2), date(2026, 9, 1), "utility"), (date(2026, 9, 1), date(2026, 9, 1), "other")]:
                with self.subTest(start=start, mode=mode), self.assertRaises(HTTPException) as error:
                    main.utility_payment_calendar(start, end, mode, session)
                self.assertEqual(error.exception.status_code, 400)
            single = main.utility_payment_calendar(date(2026, 9, 1), date(2026, 9, 1), "utility", session)
            self.assertEqual(single["dates"], ["2026-09-01"])

    def test_calendar_requires_owner_session(self):
        for role, expected in [(None, 401), ("guest", 403), ("owner", 200)]:
            with self.subTest(role=role), self.Session() as session:
                headers = []
                if role:
                    issued = issue_session(session, role)
                    headers.append((b"cookie", f"rental_manager_panel_session={issued.token}".encode("ascii")))
                scope = {"type": "http", "method": "GET", "path": "/api/utilities/calendar", "headers": headers, "query_string": b"start=2026-09-01&end=2026-09-30", "server": ("test", 80), "client": ("127.0.0.1", 1234), "scheme": "http"}

                async def call_next(_):
                    return JSONResponse({"ok": True})

                with patch.object(main, "SessionLocal", self.Session):
                    response = asyncio.run(main.panel_auth_middleware(Request(scope), call_next))
                self.assertEqual(response.status_code, expected)
