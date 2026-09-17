import json
from datetime import date, timedelta

from tests.test_billing import DatabaseTestCase
from rental_manager.models import Apartment, Lease, Meter, MeterReading, RentalObject, Tariff, Tenant, UtilityBill, UtilityBillLine, UtilityService
from rental_manager.services.billing import calculate_utility_bill, utility_line_period
from rental_manager.main import last_lease_service_period_end, lease_utility_advance_period, utility_message_projection_context, utility_issue_targets_for_bills


class UtilityPeriodRegressions(DatabaseTestCase):
    start = date(2026, 6, 26)
    end = date(2026, 9, 17)

    def fixture(self, session):
        house = RentalObject(name="Белый дом", short_code="БД")
        apartment = Apartment(object=house, name="БД3", active=True, odn_share_percent=100)
        service = UtilityService(object=house, name="Вода", kind="water")
        session.add_all([house, apartment, service])
        session.flush()
        meter = Meter(service_id=service.id, object_id=house.id, scope="object", name="Общий", active=True)
        session.add_all([meter, Tariff(service_id=service.id, starts_on=date(2026, 1, 1), tiers_json='[{"limit": null, "price": 1}]')])
        session.flush()
        session.add_all([MeterReading(meter_id=meter.id, reading_date=self.start, value=0), MeterReading(meter_id=meter.id, reading_date=self.end, value=(self.end-self.start).days * 10)])
        session.flush()
        return apartment, service

    def lease(self, session, apartment, start, end=None, tenant=None):
        lease = Lease(apartment=apartment, tenant=tenant or Tenant(full_name="Жилец"), start_date=start, end_date=end, active=end is None, payment_day=1, ip_amount=100, personal_amount=0)
        session.add(lease)
        session.flush()
        return lease

    def test_turnover_vacancy_and_closed_leases_only_charge_occupied_days(self):
        for departure, arrival in [(date(2026, 9, 7), date(2026, 9, 15)), (date(2026, 8, 21), date(2026, 8, 25)), (date(2026, 8, 21), date(2026, 8, 22))]:
            with self.subTest(departure=departure, arrival=arrival), self.Session() as session:
                apartment, service = self.fixture(session)
                old = self.lease(session, apartment, date(2026, 5, 1), departure)
                new = self.lease(session, apartment, arrival)
                bill, _ = calculate_utility_bill(session, service.id, self.start, self.end)
                totals = {lease.id: sum(line.total_amount for line in bill.lines if line.lease_id == lease.id) for lease in [old, new]}
                self.assertEqual(totals[old.id], ((departure + timedelta(days=1)) - self.start).days * 10)
                self.assertEqual(totals[new.id], (self.end - arrival).days * 10)
                for line in bill.lines:
                    start, end = utility_line_period(line)
                    self.assertGreaterEqual(start, line.lease.start_date if line.lease else arrival if line.lease_id == new.id else self.start)
                    self.assertLessEqual(end, self.end)

    def test_issued_segment_does_not_cover_whole_parent_bill(self):
        for structured in [False, True]:
            with self.subTest(structured=structured), self.Session() as session:
                apartment, service = self.fixture(session)
                lease = self.lease(session, apartment, self.start)
                issued = UtilityBill(service=service, period_start=self.start, period_end=self.end, status="issued")
                boundary = date(2026, 8, 1)
                issued.lines.append(UtilityBillLine(apartment=apartment, lease=lease, status="issued", total_amount=360, note="26.06.2026 -> 01.08.2026 (36 дн.)" if not structured else "", metadata_json=json.dumps({"line_period_start": self.start.isoformat(), "line_period_end": boundary.isoformat()}) if structured else "{}"))
                session.add(issued)
                session.flush()
                bill, _ = calculate_utility_bill(session, service.id, self.start, self.end)
                self.assertEqual(sum(line.total_amount for line in bill.lines), (self.end-boundary).days * 10)
                self.assertEqual(utility_line_period(bill.lines[0])[0], boundary)

    def test_cancelled_charge_does_not_suppress_new_calculation(self):
        with self.Session() as session:
            apartment, service = self.fixture(session)
            lease = self.lease(session, apartment, self.start)
            old = UtilityBill(service=service, period_start=self.start, period_end=self.end, status="issued")
            old.lines.append(UtilityBillLine(apartment=apartment, lease=lease, status="cancelled", total_amount=830))
            session.add(old)
            session.flush()
            bill, _ = calculate_utility_bill(session, service.id, self.start, self.end)
            self.assertEqual(sum(line.total_amount for line in bill.lines), 830)

    def test_overlapping_leases_fail_with_actionable_error(self):
        with self.Session() as session:
            apartment, service = self.fixture(session)
            self.lease(session, apartment, self.start)
            self.lease(session, apartment, date(2026, 9, 15))
            with self.assertRaisesRegex(ValueError, "Пересекаются договоры квартиры БД3"):
                calculate_utility_bill(session, service.id, self.start, self.end)

    def test_advance_never_starts_before_move_in(self):
        lease = Lease(start_date=date(2026, 9, 20))
        self.assertEqual(lease_utility_advance_period(lease, self.end, date(2026, 10, 17)), (date(2026, 9, 20), date(2026, 10, 17)))
        lease.start_date = date(2026, 11, 1)
        self.assertIsNone(lease_utility_advance_period(lease, self.end, date(2026, 10, 17)))

    def test_move_out_baseline_uses_usage_segment_not_advance_bill(self):
        with self.Session() as session:
            apartment, service = self.fixture(session)
            lease = self.lease(session, apartment, self.start, date(2026, 9, 7))
            for kind, boundary in [("utility", date(2026, 8, 1)), ("advance", self.end)]:
                bill = UtilityBill(service=service, period_start=self.start, period_end=self.end, status="issued", bill_type=kind)
                bill.lines.append(UtilityBillLine(apartment=apartment, lease=lease, status="issued", line_type="advance" if kind == "advance" else "usage", total_amount=100, metadata_json=json.dumps({"line_period_start": self.start.isoformat(), "line_period_end": boundary.isoformat()})))
                session.add(bill)
            session.flush()
            self.assertEqual(last_lease_service_period_end(session, lease, service.id), date(2026, 8, 1))

    def test_move_on_september_eighth_keeps_white_house_usage_with_old_lease(self):
        with self.Session() as session:
            apartment, service = self.fixture(session)
            old = self.lease(session, apartment, date(2026, 5, 1), date(2026, 9, 7))
            black = Apartment(object=RentalObject(name="Чёрный дом", short_code="ЧД"), name="ЧД1")
            session.add(black)
            session.flush()
            moved = self.lease(session, black, date(2026, 9, 8), tenant=old.tenant)
            replacement = self.lease(session, apartment, date(2026, 9, 15))
            bill, _ = calculate_utility_bill(session, service.id, self.start, self.end)
            self.assertEqual({line.lease_id for line in bill.lines}, {old.id, replacement.id})
            self.assertNotIn(moved.id, {line.lease_id for line in bill.lines})
            self.assertEqual(sum(line.total_amount for line in bill.lines if line.lease_id == old.id), 740)
            self.assertEqual(sum(line.total_amount for line in bill.lines if line.lease_id == replacement.id), 20)

    def test_old_debt_is_labelled_and_other_house_excluded(self):
        with self.Session() as session:
            apartment, service = self.fixture(session)
            lease = self.lease(session, apartment, self.start, date(2026, 9, 7))
            other_house = RentalObject(name="Чёрный дом", short_code="ЧД")
            other_apartment = Apartment(object=other_house, name="ЧД1")
            other_service = UtilityService(object=other_house, name="Вода", kind="water")
            session.add_all([other_apartment, other_service])
            session.flush()
            moved = self.lease(session, other_apartment, date(2026, 9, 8), tenant=lease.tenant)
            bills = []
            for current_service, current_lease, status in [(service, lease, "draft"), (service, lease, "issued"), (other_service, moved, "issued")]:
                bill = UtilityBill(service=current_service, period_start=self.start, period_end=self.end, status=status)
                bill.lines.append(UtilityBillLine(apartment=current_lease.apartment, lease=current_lease, status=status, total_amount=100, paid_amount=0, due_date=date(2026, 5, 1)))
                session.add(bill)
                bills.append(bill)
            session.flush()
            current, debt, foreign = [bill.lines[0] for bill in bills]
            context = utility_message_projection_context(session, [current], [debt, current], {debt.id: 100, current.id: 100})
            self.assertIn("Ранее выставленный долг:", context["utility_debt_details"])
            targets = utility_issue_targets_for_bills(session, [bills[0]])
            self.assertIn(debt.id, targets[0]["all_line_ids"])
            self.assertNotIn(foreign.id, targets[0]["all_line_ids"])
