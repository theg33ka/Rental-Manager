from datetime import date, datetime

from fastapi import HTTPException
from sqlalchemy import func, select

from rental_manager import main
from rental_manager.models import AiActionLog, Apartment, Expense, Lease, PaymentReceipt, RentalObject, RentCharge, Tenant, UtilityAdvanceLedger, UtilityBill, UtilityBillLine, UtilityService
from rental_manager.services.billing import generate_rent_charges
from rental_manager.services.hermes.safety import ACTION_SAFETY_REGISTRY
from rental_manager.services.lease_transfer_finance import transfer_advance
from rental_manager.services.transfer_repair import repair_state
from tests.test_billing import DatabaseTestCase


class TransferFinanceRepairTests(DatabaseTestCase):
    def fixture(self, session):
        house = RentalObject(name='Test')
        source = Apartment(object=house, name='A', active=True)
        target = Apartment(object=house, name='B', active=True)
        funding = Apartment(object=house, name='C', active=True)
        tenant = Tenant(full_name='Moved')
        old = Lease(apartment=source, tenant=tenant, start_date=date(2026, 8, 8),
                    end_date=date(2026, 10, 7), active=False, payment_day=6, ip_amount=20000, personal_amount=5000)
        new = Lease(apartment=target, tenant=tenant, start_date=date(2026, 10, 8),
                    active=True, payment_day=6, ip_amount=20000, personal_amount=5000)
        expense = Expense(apartment=source, object=house, amount=9800, expense_date=date(2026, 10, 18),
                          source_funds='rental_budget', description='From C budget')
        service = UtilityService(object=house, name='Electricity', kind='electricity')
        session.add_all([old, new, funding, expense, service])
        session.flush()
        paid = RentCharge(lease=old, period_start=date(2026, 10, 6), period_end=date(2026, 11, 5),
                          due_date=date(2026, 10, 6), ip_due=20000, personal_due=5000, ip_paid=20000, personal_paid=5000)
        ghost = RentCharge(lease=old, period_start=date(2026, 11, 6), period_end=date(2026, 12, 5),
                           due_date=date(2026, 11, 6), ip_due=20000, personal_due=5000, ip_paid=9800)
        session.add_all([paid, ghost])
        session.flush()
        session.add_all([PaymentReceipt(lease_id=old.id, apartment_id=source.id, rent_charge_id=paid.id,
                                       channel=channel, amount=amount, status='accepted')
                        for channel, amount in [('ip', 20000), ('personal', 5000)]])
        session.add(PaymentReceipt(lease_id=old.id, apartment_id=source.id, rent_charge_id=ghost.id,
                                   channel='ip', amount=9800, status='accepted', source=f'expense_credit:{expense.id}', is_expense=True))
        session.add(UtilityAdvanceLedger(lease_id=old.id, apartment_id=source.id, kind='transfer_in', amount=2951))
        session.add(UtilityAdvanceLedger(lease_id=old.id, apartment_id=source.id, kind='advance_applied', amount=-570))
        pairs = []
        for kind, amount in [('usage', 260), ('usage', 460), ('advance', 712.05)]:
            bill = UtilityBill(service=service, period_start=date(2026, 8, 8), period_end=date(2026, 9, 17), status='issued')
            line = UtilityBillLine(bill=bill, apartment=source, lease=old, line_type=kind, total_amount=amount, paid_amount=2*amount)
            session.add(line)
            session.flush()
            receipts = [PaymentReceipt(apartment_id=source.id, lease_id=old.id, utility_line_id=line.id,
                                       channel='utilities', amount=amount, status='accepted', source='telegram',
                                       paid_at=datetime(2026, 9, 21, 16, 22)) for _ in range(2)]
            session.add_all(receipts)
            session.flush()
            pairs.append([r.id for r in receipts])
        bill = UtilityBill(service=service, period_start=date(2026, 9, 17), period_end=date(2026, 10, 28), status='issued')
        session.add(UtilityBillLine(bill=bill, apartment=target, lease=new, line_type='usage', total_amount=590, paid_amount=0))
        forecast_bill = UtilityBill(service=service, period_start=date(2026, 10, 28), period_end=date(2026, 11, 28), status='issued', bill_type='advance')
        session.add(UtilityBillLine(bill=forecast_bill, apartment=target, lease=new, line_type='advance', total_amount=928.68, paid_amount=0))
        stranger = Lease(apartment=target, tenant=Tenant(full_name='Other'), start_date=date(2025, 1, 1),
                         end_date=date(2025, 12, 1), active=False, payment_day=1)
        session.add(UtilityBillLine(bill=bill, apartment=target, lease=stranger, line_type='usage', total_amount=1000, paid_amount=0))
        session.add(UtilityAdvanceLedger(lease=stranger, apartment=target, amount=500))
        session.flush()
        args = dict(source_lease_id=old.id, target_lease_id=new.id, expense_id=expense.id,
                    funding_apartment_id=funding.id, duplicate_pairs=pairs)
        session.commit()
        args['expected_fingerprint'] = repair_state(session, args)
        return args, old, new, paid, ghost, stranger

    def test_preview_repair_and_retry_preserve_money_history_and_paid_period(self):
        with self.Session() as session:
            args, old, new, paid, ghost, stranger = self.fixture(session)
            count = session.scalar(select(func.count()).select_from(PaymentReceipt))
            preview = main.repair_transfer_finance(args, session, preview=True)
            self.assertEqual(preview['transferred'], 3093.05)
            self.assertEqual(preview['advance_after'], 2503.05)
            self.assertEqual(preview['advance_requests'][0]['after'], 0)
            self.assertEqual(repair_state(session, args), args['expected_fingerprint'])
            self.assertEqual(session.scalar(select(func.count()).select_from(AiActionLog)), 0)
            self.assertEqual(main.repair_transfer_finance(args, session), preview)
            self.assertTrue(main.repair_transfer_finance(args, session)['already_applied'])
            generate_rent_charges(session)
            self.assertEqual(ghost.ip_due + ghost.personal_due, 0)
            self.assertEqual((paid.ip_paid, paid.personal_paid, paid.period_end), (20000, 5000, date(2026, 11, 5)))
            self.assertEqual(main.utility_advance_balance(session, old.apartment_id, old.id), 0)
            self.assertEqual(main.utility_advance_balance(session, new.apartment_id, new.id), 2503.05)
            self.assertEqual(main.utility_advance_balance(session, new.apartment_id, stranger.id), 500)
            self.assertEqual(session.scalar(select(func.count()).select_from(PaymentReceipt)), count)
            self.assertEqual(session.scalar(select(func.count()).select_from(AiActionLog)), 1)
            self.assertEqual(session.get(Expense, args['expense_id']).amount, 9800)
            self.assertEqual(min(c.due_date for c in new.rent_charges), date(2026, 11, 6))
            with self.assertRaises(HTTPException):
                main.credit_expense_rent(args['expense_id'], session)

    def test_changed_snapshot_and_invalid_duplicate_have_no_mutations(self):
        with self.Session() as session:
            args, old, *_ = self.fixture(session)
            session.get(Expense, args['expense_id']).amount = 9801
            session.flush()
            with self.assertRaises(HTTPException):
                main.repair_transfer_finance(args, session)
            self.assertEqual(main.utility_advance_balance(session, old.apartment_id, old.id), 2381)
            args['expected_fingerprint'] = repair_state(session, args)
            with self.assertRaises(HTTPException):
                main.repair_transfer_finance(args, session)
            self.assertEqual(session.scalar(select(func.count()).select_from(AiActionLog)), 0)

    def test_invalid_pairs_and_other_tenant_fail_closed(self):
        with self.Session() as session:
            args, old, new, _, _, stranger = self.fixture(session)
            args['duplicate_pairs'][1][1] = args['duplicate_pairs'][0][1]
            with self.assertRaises(HTTPException):
                main.repair_transfer_finance(args, session)
            with self.assertRaises(ValueError):
                transfer_advance(session, old, stranger)
            self.assertEqual(main.utility_advance_balance(session, old.apartment_id, old.id), 2381)
            self.assertEqual(main.utility_advance_balance(session, new.apartment_id, new.id), 0)

    def test_critical_confirmation_required(self):
        decision = ACTION_SAFETY_REGISTRY.classify('repair_transfer_finance')
        self.assertEqual(decision.level, 3)
        self.assertTrue(decision.confirmation_required)

    def fresh_lease(self, session, amount=500):
        house = RentalObject(name='Fresh')
        source = Apartment(object=house, name='Source', active=True)
        target = Apartment(object=house, name='Target', active=True)
        lease = Lease(apartment=source, tenant=Tenant(full_name='Original'), start_date=date(2026, 1, 1),
                      payment_day=6, ip_amount=100, personal_amount=0, active=True)
        session.add_all([lease, target])
        session.flush()
        receipt = PaymentReceipt(lease_id=lease.id, apartment_id=source.id, amount=amount,
                                 channel='utility_advance', status='accepted')
        stranger = Lease(apartment=source, tenant=Tenant(full_name='Other'), start_date=date(2025, 1, 1),
                         end_date=date(2025, 12, 1), active=False, payment_day=1)
        session.add_all([receipt, UtilityAdvanceLedger(lease=stranger, apartment=source, amount=700)])
        session.commit()
        return lease, target, receipt, stranger

    def test_normal_transfer_moves_existing_money_once_without_other_tenants_advance(self):
        with self.Session() as session:
            old, target, receipt, stranger = self.fresh_lease(session)
            result = main.transfer_lease(old.id, {'apartment_id': target.id, 'transfer_date': '2026-04-08'}, session)
            new = session.get(Lease, result['new_lease']['id'])
            self.assertEqual(result['summary']['utility_advance_transferred'], 500)
            self.assertEqual(transfer_advance(session, old, new), 0)
            self.assertEqual(main.utility_advance_balance(session, old.apartment_id, old.id), 0)
            self.assertEqual(main.utility_advance_balance(session, target.id, new.id), 500)
            self.assertEqual(main.utility_advance_balance(session, old.apartment_id, stranger.id), 700)
            self.assertEqual(session.scalar(select(func.count()).select_from(PaymentReceipt)), 1)
            self.assertEqual(receipt.amount, 500)
            with self.assertRaises(HTTPException):
                main.update_payment_receipt(receipt.id, {'amount': 100}, session)

    def test_negative_source_balance_rolls_back_entire_transfer(self):
        with self.Session() as session:
            old, target, _, _ = self.fresh_lease(session)
            session.add(UtilityAdvanceLedger(lease_id=old.id, apartment_id=old.apartment_id, amount=-600))
            session.commit()
            count = session.scalar(select(func.count()).select_from(Lease))
            with self.assertRaises(HTTPException):
                main.transfer_lease(old.id, {'apartment_id': target.id, 'transfer_date': '2026-04-08'}, session)
            self.assertEqual(session.scalar(select(func.count()).select_from(Lease)), count)
            self.assertTrue(old.active)
            self.assertIsNone(old.end_date)

    def test_prepaid_future_rent_requires_separate_allocation_before_transfer(self):
        with self.Session() as session:
            old, target, _, _ = self.fresh_lease(session)
            generate_rent_charges(session, until=date(2026, 5, 6))
            future = session.scalar(select(RentCharge).where(RentCharge.lease_id == old.id, RentCharge.due_date == date(2026, 5, 6)))
            session.add(PaymentReceipt(lease_id=old.id, apartment_id=old.apartment_id, rent_charge_id=future.id,
                                       channel='ip', amount=100, status='accepted'))
            session.commit()
            with self.assertRaises(HTTPException):
                main.transfer_lease(old.id, {'apartment_id': target.id, 'transfer_date': '2026-04-08'}, session)
            self.assertIsNone(old.end_date)
