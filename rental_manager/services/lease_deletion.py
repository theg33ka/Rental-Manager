from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from rental_manager.models import (
    AgentMemory, AiSkill, DomainEvent, Lease, ManualDebt, MessageLog,
    OperationalCase, OwnerPreference, PaymentReceipt, PaymentSituation,
    ReminderOutcome, RentCharge, TenantStrategyProfile, UtilityAdvanceLedger, utc_now,
)


def prepare_lease_deletion(session: Session, lease_id: int) -> None:
    charge_ids = select(RentCharge.id).where(RentCharge.lease_id == lease_id)
    receipt_ids = select(PaymentReceipt.id).where(
        or_(PaymentReceipt.lease_id == lease_id, PaymentReceipt.rent_charge_id.in_(charge_ids))
    )
    message_ids = select(MessageLog.id).where(
        or_(MessageLog.lease_id == lease_id, MessageLog.rent_charge_id.in_(charge_ids))
    )
    situation_ids = select(PaymentSituation.id).where(PaymentSituation.lease_id == lease_id)
    memory_ids = select(AgentMemory.id).where(AgentMemory.lease_id == lease_id)
    session.execute(delete(ReminderOutcome).where(ReminderOutcome.contract_id == lease_id))
    session.execute(update(ReminderOutcome).where(ReminderOutcome.message_log_id.in_(message_ids)).values(message_log_id=None))
    session.execute(update(ReminderOutcome).where(ReminderOutcome.payment_situation_id.in_(situation_ids)).values(payment_situation_id=None))
    session.execute(delete(TenantStrategyProfile).where(TenantStrategyProfile.contract_id == lease_id))
    session.execute(delete(ManualDebt).where(ManualDebt.lease_id == lease_id))
    for model in (OwnerPreference, AiSkill):
        session.execute(update(model).where(model.legacy_memory_id.in_(memory_ids)).values(legacy_memory_id=None))
    # Сохраняем движения авансов, отвязывая удаляемые договор и чеки.
    session.execute(update(UtilityAdvanceLedger).where(UtilityAdvanceLedger.lease_id == lease_id).values(lease_id=None))
    session.execute(update(UtilityAdvanceLedger).where(UtilityAdvanceLedger.payment_receipt_id.in_(receipt_ids)).values(payment_receipt_id=None))
    session.execute(delete(MessageLog).where(MessageLog.id.in_(message_ids)))


def detach_lease_audit(session: Session, lease: Lease, *, delete_tenant: bool) -> None:
    # Flush сохраняет события, созданные ORM при очистке связанных записей.
    session.flush()
    session.flush()
    session.execute(update(DomainEvent).where(DomainEvent.contract_id == lease.id).values(contract_id=None))
    session.execute(update(OperationalCase).where(OperationalCase.contract_id == lease.id).values(
        contract_id=None, status="resolved", resolved_at=utc_now(),
        resolution_reason="Договор удалён", next_review_at=None,
    ))
    if delete_tenant:
        for model in (DomainEvent, OperationalCase):
            session.execute(update(model).where(model.tenant_id == lease.tenant_id).values(tenant_id=None))
