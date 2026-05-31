from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from rental_manager.models import Expense, Lease, ManualDebt, MessageLog, PaymentReceipt, RentCharge, UtilityBillLine
from rental_manager.services.billing import update_rent_charge_status, update_utility_line_status


def money_text(value: float) -> str:
    return f"{float(value or 0):,.2f}".replace(",", " ").replace(".", ",") + " руб."


def _status_text(value: str) -> str:
    return {
        "pending": "ожидается",
        "partial": "частично оплачено",
        "paid": "оплачено",
        "paid_ahead": "оплачено вперёд",
        "overdue": "просрочено",
        "deferred": "отсрочка",
        "issued": "выставлено",
        "suspicious": "нужна проверка",
        "accepted": "принято",
        "duplicate": "дубликат",
    }.get(value or "", value or "неизвестно")


def tenant_context_text(session: Session, lease: Lease, settings: dict[str, Any], today: date | None = None) -> str:
    today = today or date.today()
    rent_lines = []
    charges = session.scalars(
        select(RentCharge).where(RentCharge.lease_id == lease.id).order_by(RentCharge.due_date, RentCharge.id)
    ).all()
    for charge in charges:
        update_rent_charge_status(charge, today)
        debt = max(0.0, float(charge.ip_due or 0) - float(charge.ip_paid or 0)) + max(0.0, float(charge.personal_due or 0) - float(charge.personal_paid or 0))
        if debt > 0.009 or charge.due_date >= today:
            rent_lines.append(
                f"- {charge.due_date:%d.%m.%Y}: {_status_text(charge.status)}, долг {money_text(debt)}, "
                f"ИП осталось {money_text(max(0.0, float(charge.ip_due or 0) - float(charge.ip_paid or 0)))}, "
                f"перевод осталось {money_text(max(0.0, float(charge.personal_due or 0) - float(charge.personal_paid or 0)))}"
            )
        if len(rent_lines) >= 6:
            break

    utility_lines = []
    lines = session.scalars(
        select(UtilityBillLine)
        .options(joinedload(UtilityBillLine.bill))
        .where(UtilityBillLine.lease_id == lease.id)
        .order_by(UtilityBillLine.due_date, UtilityBillLine.id)
    ).all()
    for line in lines:
        update_utility_line_status(line, today)
        debt = max(0.0, float(line.total_amount or 0) - float(line.paid_amount or 0))
        if debt > 0.009:
            due = f"{line.due_date:%d.%m.%Y}" if line.due_date else "срок не задан"
            service = line.bill.service.name if line.bill and line.bill.service else "коммунальная услуга"
            utility_lines.append(f"- {service}, срок {due}: {_status_text(line.status)}, долг {money_text(debt)}")
        if len(utility_lines) >= 6:
            break

    manual_lines = []
    debts = session.scalars(
        select(ManualDebt).where(ManualDebt.lease_id == lease.id, ManualDebt.active.is_(True)).order_by(ManualDebt.due_date, ManualDebt.id)
    ).all()
    for debt in debts:
        left = max(0.0, float(debt.amount or 0) - float(debt.paid_amount or 0))
        if left > 0.009:
            manual_lines.append(f"- {debt.title or debt.kind}: долг {money_text(left)}")
        if len(manual_lines) >= 5:
            break

    receipt_lines = []
    receipts = session.scalars(
        select(PaymentReceipt).where(PaymentReceipt.lease_id == lease.id).order_by(PaymentReceipt.paid_at.desc(), PaymentReceipt.id.desc()).limit(5)
    ).all()
    for receipt in receipts:
        receipt_lines.append(f"- {receipt.paid_at:%d.%m.%Y}: {money_text(receipt.amount)}, канал {receipt.channel}, статус {_status_text(receipt.status)}")

    message_lines = []
    logs = session.scalars(
        select(MessageLog).where(MessageLog.lease_id == lease.id).order_by(MessageLog.created_at.desc(), MessageLog.id.desc()).limit(5)
    ).all()
    for log in logs:
        message_lines.append(f"- {log.created_at:%d.%m.%Y}: {log.template_key or 'сообщение'}, статус {_status_text(log.status)}")

    total_rent = money_text(float(lease.ip_amount or 0) + float(lease.personal_amount or 0))
    return "\n".join(
        [
            "Контекст арендатора:",
            f"Сегодня: {today:%d.%m.%Y}",
            f"Жилец: {lease.tenant.full_name}",
            f"Объект: {lease.apartment.object.name}",
            f"Квартира: {lease.apartment.name}",
            f"День оплаты аренды: {lease.payment_day}",
            f"Ежемесячно: всего {total_rent}, ИП {money_text(lease.ip_amount)}, перевод {money_text(lease.personal_amount)}",
            "",
            "Аренда:",
            "\n".join(rent_lines) if rent_lines else "- открытых начислений не видно",
            "",
            "Коммуналка:",
            "\n".join(utility_lines) if utility_lines else "- открытых долгов не видно",
            "",
            "Ручные долги:",
            "\n".join(manual_lines) if manual_lines else "- открытых ручных долгов не видно",
            "",
            "Реквизиты:",
            f"- ИП получатель: {settings.get('ip_recipient_name') or 'не задан'}",
            f"- ИП счёт: {settings.get('ip_recipient_account') or 'не задан'}",
            f"- Перевод: {settings.get('personal_recipient_phone') or 'не задан'} {settings.get('personal_recipient_bank') or ''}".strip(),
            "",
            "Последние чеки:",
            "\n".join(receipt_lines) if receipt_lines else "- чеков в системе не видно",
            "",
            "Последние сообщения:",
            "\n".join(message_lines) if message_lines else "- сообщений ещё не было",
        ]
    )


def owner_context_text(session: Session, dashboard: dict[str, Any], today: date | None = None) -> str:
    today = today or date.today()

    def issue_lines(key: str, label: str, limit: int = 8) -> list[str]:
        result = [f"{label}: {len(dashboard.get(key, []))}"]
        for item in dashboard.get(key, [])[:limit]:
            tenant = item.get("tenant") or item.get("name") or ""
            apartment = item.get("apartment") or ""
            debt = item.get("debt") or item.get("amount") or item.get("total_cost") or 0
            due = item.get("due_date") or item.get("last_date") or item.get("period_end") or ""
            result.append(f"- {apartment} {tenant}: {money_text(float(debt or 0))}, дата {due}")
        return result

    pending_expenses = dashboard.get("pending_personal_expenses", [])
    expense_lines = ["Личные расходы к компенсации: " + str(len(pending_expenses))]
    for item in pending_expenses[:8]:
        expense_lines.append(f"- {item.get('category') or 'расход'}: {money_text(float(item.get('amount') or 0))}, {item.get('description') or ''}")

    return "\n".join(
        [
            f"Контекст владельца на {today:%d.%m.%Y}:",
            "",
            *issue_lines("rent_today", "Аренда сегодня"),
            "",
            *issue_lines("rent_overdue", "Просроченная аренда"),
            "",
            *issue_lines("rent_partial", "Частичная аренда"),
            "",
            *issue_lines("utility_overdue", "Просроченная коммуналка"),
            "",
            *issue_lines("utility_issued", "Выставленная коммуналка"),
            "",
            *issue_lines("provider_debts", "Поставщики не оплачены"),
            "",
            *issue_lines("suspicious_receipts", "Подозрительные чеки"),
            "",
            *issue_lines("stale_readings", "Старые показания"),
            "",
            *expense_lines,
            "",
            "Открытые месячные отчёты: " + str(len(dashboard.get("monthly_reports", []))),
        ]
    )
