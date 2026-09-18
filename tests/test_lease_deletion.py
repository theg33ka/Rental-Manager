import unittest
from datetime import date

from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session

from rental_manager.database import Base
from rental_manager.main import delete_lease
from rental_manager.models import (
    AgentMemory, AiSkill, Apartment, DomainEvent, Lease, ManualDebt, MessageLog, OperationalCase, OwnerPreference,
    PaymentReceipt, PaymentSituation, ReminderOutcome, RentalObject,
    RentCharge, Tenant, TenantStrategyProfile, UtilityAdvanceLedger,
)
from rental_manager.services.hermes.events import _after_flush_postexec, _before_flush


class LeaseDeletionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        event.listen(self.engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
        Base.metadata.create_all(self.engine)
        class DeletionSession(Session):
            pass

        self.Session = DeletionSession
        event.listen(self.Session, "before_flush", _before_flush)
        event.listen(self.Session, "after_flush_postexec", _after_flush_postexec)

    def tearDown(self):
        self.engine.dispose()

    def test_delete_with_financial_and_hermes_references(self):
        for shared_tenant in (False, True):
            with self.subTest(shared_tenant=shared_tenant), self.Session(self.engine, autoflush=False) as session:
                apartment = Apartment(object=RentalObject(name=f"Дом {shared_tenant}"), name="БД1")
                lease = Lease(apartment=apartment, tenant=Tenant(full_name="Тест"), start_date=date(2026, 1, 1), payment_day=1)
                session.add(lease)
                session.flush()
                lease_id, tenant_id = lease.id, lease.tenant_id
                other = None
                if shared_tenant:
                    other = Lease(apartment=apartment, tenant=lease.tenant, start_date=date(2026, 2, 1), payment_day=1)
                    session.add(other)
                charge = RentCharge(lease=lease, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31), due_date=date(2026, 1, 1))
                session.add(charge)
                session.flush()
                receipt = PaymentReceipt(rent_charge_id=charge.id, amount=100, channel="personal")
                message = MessageLog(rent_charge_id=charge.id, channel="telegram", status="sent")
                situation = PaymentSituation(lease_id=lease_id, kind="rent", reference_id=charge.id)
                case = OperationalCase(case_key=f"test:{shared_tenant}:{lease_id}", case_type="rent", contract_id=lease_id, tenant_id=tenant_id)
                session.add_all([receipt, message, situation, case])
                session.flush()
                ledger = UtilityAdvanceLedger(apartment_id=apartment.id, lease_id=lease_id, payment_receipt_id=receipt.id, amount=100)
                memory = AgentMemory(lease_id=lease_id, content="Тест")
                session.add(memory)
                session.flush()
                preference = OwnerPreference(scope=f"test:{shared_tenant}", key="test", legacy_memory_id=memory.id)
                skill = AiSkill(name=f"test:{shared_tenant}", legacy_memory_id=memory.id)
                session.add_all([
                    ledger, preference, skill,
                    ManualDebt(lease=lease, apartment_id=apartment.id, amount=500),
                    TenantStrategyProfile(contract_id=lease_id, tenant_id=tenant_id),
                    ReminderOutcome(contract_id=lease_id, payment_situation_id=situation.id, message_log_id=message.id),
                ])
                session.commit()
                # Проверяем и ранее загруженные ORM-коллекции.
                list(lease.manual_debts)
                list(lease.rent_charges)
                self.assertEqual(delete_lease(lease_id, session), {"ok": True})
                session.expire_all()
                self.assertIsNone(session.get(Lease, lease_id))
                self.assertEqual(session.get(Tenant, tenant_id) is not None, shared_tenant)
                self.assertIsNone(ledger.lease_id)
                self.assertIsNone(ledger.payment_receipt_id)
                self.assertEqual(ledger.amount, 100)
                self.assertEqual(case.status, "resolved")
                self.assertIsNone(case.contract_id)
                self.assertIsNone(preference.legacy_memory_id)
                self.assertIsNone(skill.legacy_memory_id)
                self.assertTrue(session.scalars(select(DomainEvent)).all())
                self.assertEqual(session.execute(text("PRAGMA foreign_key_check")).all(), [])
                if other:
                    self.assertIsNotNone(session.get(Lease, other.id))
