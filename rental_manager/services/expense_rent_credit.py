from datetime import datetime, time
import math

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from rental_manager.models import Expense, Lease, PaymentReceipt
from rental_manager.services.billing import IGNORE_LEASE_MARK, money
from rental_manager.services.payment_allocation import create_rent_receipts


CREDIT_SOURCE = "expense_credit:"
PAYMENT_EXPENSE_MARK = "PAYMENT_RECEIPT_EXPENSE:"


def expense_rent_credit_amount(session: Session, expense: Expense) -> float:
    if (expense.notes or "").startswith(PAYMENT_EXPENSE_MARK):
        return money(expense.amount)
    return money(session.scalar(select(func.coalesce(func.sum(PaymentReceipt.amount), 0)).where(
        PaymentReceipt.source == f"{CREDIT_SOURCE}{expense.id}",
        PaymentReceipt.status == "accepted",
    )) or 0)


def credit_expense_to_rent(session: Session, expense_id: int) -> list[PaymentReceipt]:
    expense = session.scalar(select(Expense).where(Expense.id == expense_id).with_for_update())
    if not expense:
        raise ValueError("Расход не найден")
    if expense.source_funds != "rental_budget":
        raise ValueError("В аренду засчитываются только расходы из арендного бюджета")
    if not math.isfinite(expense.amount) or expense.amount <= 0:
        raise ValueError("Сумма расхода должна быть положительным числом")
    remaining = money(expense.amount - expense_rent_credit_amount(session, expense))
    if remaining <= 0:
        return []
    if not expense.apartment_id:
        raise ValueError("Для зачёта расхода выберите квартиру жильца")
    leases = session.scalars(select(Lease).where(
        Lease.apartment_id == expense.apartment_id,
        Lease.start_date <= expense.expense_date,
        or_(Lease.end_date.is_(None), Lease.end_date >= expense.expense_date),
    )).all()
    leases = [lease for lease in leases if IGNORE_LEASE_MARK not in (lease.notes or "")]
    if len(leases) != 1:
        raise ValueError("На дату расхода должен быть ровно один учитываемый договор в квартире")
    lease = leases[0]
    if expense.object_id and expense.object_id != lease.apartment.object_id:
        raise ValueError("Квартира расхода относится к другому объекту")
    # Начинаем с месяца расхода, остаток идёт только в следующие ИП-платежи.
    receipts = create_rent_receipts(
        session, lease, "ip", remaining,
        paid_at=datetime.combine(expense.expense_date, time(12)),
        source=f"{CREDIT_SOURCE}{expense.id}", status="accepted",
        notes=f"Зачёт расхода: {expense.category or expense.description or 'нужды объекта'}",
        cutoff=expense.expense_date.replace(day=1),
    )
    for receipt in receipts:
        receipt.is_expense = True
    session.flush()
    return receipts
