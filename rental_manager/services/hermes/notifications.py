from __future__ import annotations

from datetime import datetime, timedelta
import json
from typing import Any, Callable, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from rental_manager.models import (AgentNotification, AppSetting, HermesAgentRun,
    MessageLog, OperationalCase, PaymentReceipt, utc_now)
from rental_manager.services.hermes.cases import OPEN_CASE_STATUSES, case_label, reconcile_operational_cases
from rental_manager.services.hermes.events import stable_hash
from rental_manager.services.hermes.memory import reconcile_owner_commitments
from rental_manager.services.telegram_bot import TelegramApiError


class NotificationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    mode: Literal["telegram", "push", "both", "critical_push"] = "telegram"
    quiet_start: int = Field(default=22, ge=0, le=23)
    quiet_end: int = Field(default=8, ge=0, le=23)
    attention_days: int = Field(default=3, ge=1, le=60)
    critical_days: int = Field(default=10, ge=1, le=365)
    critical_amount: float = Field(default=100000, ge=0)
    daily_hour: int = Field(default=19, ge=0, le=23)
    tone: Literal["calm", "conversational"] = "calm"
    historical_days: int = Field(default=30, ge=10, le=365)
    daily_case_limit: int = Field(default=5, ge=1, le=30)


def config(session: Session) -> NotificationConfig:
    row = session.get(AppSetting, "manager_notifications")
    return NotificationConfig.model_validate_json(row.value) if row else NotificationConfig()


def local_time(now: datetime) -> datetime:
    return now.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("Asia/Novosibirsk"))


def quiet(cfg: NotificationConfig, now: datetime) -> bool:
    hour = local_time(now).hour
    if cfg.quiet_start == cfg.quiet_end:
        return False
    if cfg.quiet_start < cfg.quiet_end:
        return cfg.quiet_start <= hour < cfg.quiet_end
    return hour >= cfg.quiet_start or hour < cfg.quiet_end


def importance(item: OperationalCase, cfg: NotificationConfig) -> str:
    days = int(json.loads(item.metadata_json or "{}").get("days_overdue") or 0)
    if item.case_type in {"automation_failure", "suspicious_payment"} or days >= cfg.critical_days or item.amount_total >= cfg.critical_amount:
        return "critical"
    if days >= cfg.attention_days or item.waiting_for == "owner":
        return "attention"
    return "informational"


def historical(item: OperationalCase, cfg: NotificationConfig, now: datetime) -> bool:
    metadata = json.loads(item.metadata_json or "{}")
    age = int(metadata.get("days_overdue") or 0)
    day = metadata.get("expense_date") or metadata.get("due_date")
    if day:
        try:
            age = max(age, (local_time(now).date() - datetime.fromisoformat(str(day)).date()).days)
        except ValueError:
            pass
    return age > cfg.historical_days


def channels(cfg: NotificationConfig, level: str) -> list[str]:
    if cfg.mode == "both":
        return ["telegram", "push"]
    if cfg.mode == "critical_push":
        return ["push"] if level == "critical" else ["telegram"]
    return [cfg.mode]


def queue(session: Session, *, key: str, text: str, level: str, cfg: NotificationConfig,
          case_id: int | None = None) -> list[AgentNotification]:
    rows = []
    for channel in channels(cfg, level):
        dedupe = f"{key}:{channel}"
        row = session.scalar(select(AgentNotification).where(AgentNotification.dedupe_key == dedupe))
        if not row:
            row = AgentNotification(dedupe_key=dedupe, case_id=case_id, channel=channel,
                importance=level, text=text[:3500])
            try:
                with session.begin_nested():
                    session.add(row)
                    session.flush()
            except IntegrityError:
                row = session.scalar(select(AgentNotification).where(AgentNotification.dedupe_key == dedupe))
                if row is None:
                    raise
        rows.append(row)
    return rows


def reconcile_notifications(session: Session, *, now: datetime | None = None,
                            render: Callable[[dict[str, Any]], str] | None = None,
                            excluded_leases: set[int] | None = None,
                            daily_enabled: bool = True) -> None:
    now = now or utc_now()
    cfg = config(session)
    if not cfg.enabled:
        return
    cases = reconcile_operational_cases(session, local_time(now).date())
    excluded_leases = excluded_leases or set()
    paused_contracts = {c.contract_id for c in cases if c.contract_id and c.suppression_until and c.suppression_until > now}
    commitments = reconcile_owner_commitments(session, now=now)
    session.flush()
    pending = session.scalars(select(AgentNotification).where(
        AgentNotification.status.in_(["pending", "failed"]), AgentNotification.case_id.is_not(None))).all()
    for row in pending:
        case = session.get(OperationalCase, row.case_id)
        if not case or case.status not in OPEN_CASE_STATUSES or case.contract_id in excluded_leases:
            row.status = "cancelled"
    for item in cases:
        if item.contract_id in excluded_leases or item.contract_id in paused_contracts:
            continue
        if item.suppression_until and item.suppression_until > now:
            continue
        due = next((c for c in commitments if c.case_id == item.id and c.status == "overdue"), None)
        level = "attention" if due else importance(item, cfg)
        if level not in {"attention", "critical"}:
            continue
        metadata = json.loads(item.metadata_json or "{}")
        if not due and historical(item, cfg, now):
            continue
        today_start = local_time(now).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        count = session.scalar(select(func.count(AgentNotification.id)).where(
            AgentNotification.case_id.is_not(None), AgentNotification.created_at >= today_start)) or 0
        if count >= cfg.daily_case_limit * len(channels(cfg, level)):
            continue
        signature = stable_hash({"amount": item.amount_total, "level": level,
            "paid": metadata.get("paid_total"), "due": metadata.get("due_date"),
            "review": due.due_at.isoformat() if due and due.due_at else None})
        key = f"case:{item.id}:{signature}"
        if session.scalar(select(AgentNotification.id).where(AgentNotification.dedupe_key.like(key + ":%"))):
            continue
        latest = session.scalar(select(MessageLog).where(MessageLog.lease_id == item.contract_id)
            .order_by(MessageLog.id.desc()).limit(1)) if item.contract_id else None
        text = f"{case_label(session, item)}: {item.compact_summary}."
        if due:
            text = f"Вы просили: «{due.description}». Срок контроля наступил. {text} Возвращаемся к вопросу?"
        else:
            text += (f" Последнее сообщение: {latest.created_at:%d.%m}, статус «{latest.status}»." if latest else " Сохранённых сообщений по договору нет.")
            text += " Нужно ваше решение." if level == "critical" else " Предлагаю проверить ситуацию."
        facts = {"case_id": item.id, "facts": text, "tone": cfg.tone}
        if render:
            text = render(facts) or text
        for stale in pending:
            if stale.case_id == item.id:
                stale.status = "cancelled"
        queue(session, key=key, text=text, level=level, cfg=cfg, case_id=item.id)
    # Unavailable LLM never prevents deterministic supervision and notifications.
    last = session.scalar(select(HermesAgentRun).where(HermesAgentRun.completed_at.is_not(None))
        .order_by(HermesAgentRun.completed_at.desc(), HermesAgentRun.id.desc()).limit(1))
    if last and last.status == "failed":
        queue(session, key=f"ai-failure:{local_time(now).date()}", text="AI не смог завершить запрос. Расчёты и обычные напоминания продолжают работать. Проверьте состояние AI в панели.", level="critical", cfg=cfg)
    if daily_enabled and local_time(now).hour >= cfg.daily_hour:
        day = local_time(now).date()
        begin = local_time(now).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        payments = session.execute(select(func.count(PaymentReceipt.id), func.coalesce(func.sum(PaymentReceipt.amount), 0)).where(
            PaymentReceipt.paid_at >= begin, PaymentReceipt.paid_at < begin + timedelta(days=1),
            PaymentReceipt.status == "accepted", PaymentReceipt.is_expense.is_(False))).one()
        sent = session.scalar(select(func.count(MessageLog.id)).where(MessageLog.created_at >= begin,
            MessageLog.created_at < begin + timedelta(days=1), MessageLog.status == "sent", MessageLog.lease_id.is_not(None))) or 0
        changed = session.scalars(select(AgentNotification).where(AgentNotification.created_at >= begin,
            AgentNotification.case_id.is_not(None), AgentNotification.channel == channels(cfg, "attention")[0])).all()
        received = f"{float(payments[1]):,.2f}".replace(",", " ")
        text = f"Сегодня: принято оплат — {payments[0]} на {received} ₽; отправлено сообщений жильцам — {sent}."
        labels = list(dict.fromkeys(row.text for row in changed))[:3]
        text += "\n" + ("\n".join(labels) if labels else "Новых изменений, требующих вашего решения, нет.")
        key = f"daily:{day}"
        if not session.scalar(select(AgentNotification.id).where(AgentNotification.dedupe_key.like(key + ":%"))):
            if render:
                text = render({"facts": text, "tone": cfg.tone, "daily": True}) or text
            queue(session, key=key, text=text, level="routine", cfg=cfg)


def deliver_telegram(session: Session, send: Callable[[str], dict[str, Any]], *, now: datetime | None = None) -> int:
    now = now or utc_now()
    cfg = config(session)
    if not cfg.enabled:
        return 0
    session.execute(update(AgentNotification).where(AgentNotification.channel == "telegram",
        AgentNotification.status == "sending", ((AgentNotification.next_attempt_at <= now) | AgentNotification.next_attempt_at.is_(None))).values(
            status="uncertain", error="Отправка прервалась; проверьте Telegram перед повтором."))
    session.commit()
    rows = session.scalars(select(AgentNotification).where(AgentNotification.channel == "telegram",
        AgentNotification.status.in_(["pending", "failed"])).order_by(AgentNotification.id).limit(30)).all()
    sent = 0
    for row in rows:
        if row.next_attempt_at and row.next_attempt_at > now:
            continue
        if quiet(cfg, now) and row.importance != "critical":
            continue
        case = session.get(OperationalCase, row.case_id) if row.case_id else None
        if case and (case.status not in OPEN_CASE_STATUSES or (case.suppression_until and case.suppression_until > now)):
            continue
        claimed = session.execute(update(AgentNotification).where(AgentNotification.id == row.id,
            AgentNotification.status.in_(["pending", "failed"])).values(status="sending",
                next_attempt_at=now + timedelta(minutes=2), attempts=AgentNotification.attempts + 1))
        if not getattr(claimed, "rowcount", 0):
            continue
        session.commit()  # Persist the claim before the external call; uncertain sends are not retried blindly.
        try:
            response = send(row.text)
            if not response.get("ok"):
                raise TelegramApiError("Telegram rejected message", status_code=response.get("error_code"),
                    retry_after=int((response.get("parameters") or {}).get("retry_after") or 0))
            row.remote_message_id = str((response.get("result") or {}).get("message_id") or "")
            row.status = "sent"
            row.sent_at = now
            row.error = ""
            row.next_attempt_at = None
            sent += 1
        except Exception as exc:
            cause = exc.__cause__ or exc
            if isinstance(cause, TelegramApiError) and cause.status_code == 429:
                row.status = "failed"
                row.next_attempt_at = now + timedelta(seconds=max(60, cause.retry_after))
                row.error = "Telegram ограничил частоту; повтор после указанной задержки."
            elif isinstance(cause, TelegramApiError) and cause.status_code in {400, 401, 403, 404}:
                row.status = "rejected"
                row.error = "Telegram отклонил отправку; проверьте бота и доступ к чату."
            else:
                row.status = "uncertain"
                row.error = "Не удалось подтвердить отправку; проверьте Telegram перед повтором."
        session.commit()
    return sent


def serialize(row: AgentNotification) -> dict[str, Any]:
    return {name: value.isoformat() if isinstance(value, datetime) else value for name in
        ("id", "case_id", "channel", "importance", "text", "status", "attempts", "error", "created_at", "sent_at", "delivered_at", "read_at")
        for value in [getattr(row, name)]}


def push_feed(session: Session, *, now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or utc_now()
    cfg = config(session)
    if not cfg.enabled:
        return []
    rows = session.scalars(select(AgentNotification).where(AgentNotification.channel == "push",
        AgentNotification.status.in_(["pending", "sent"])).order_by(AgentNotification.id).limit(50)).all()
    result = []
    for row in rows:
        case = session.get(OperationalCase, row.case_id) if row.case_id else None
        if case and (case.status not in OPEN_CASE_STATUSES or (case.suppression_until and case.suppression_until > now)):
            continue
        if not quiet(cfg, now) or row.importance == "critical":
            result.append(serialize(row))
    return result
