from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from rental_manager.models import PanelLoginAttempt, PanelSession, utc_now


SESSION_TTL = timedelta(days=30)
SESSION_ACTIVITY_INTERVAL = timedelta(minutes=5)
MAX_ACTIVE_SESSIONS = 128


def token_hash(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def login_fingerprint(client_host: str, user_agent: str) -> str:
    normalized = f"{client_host.strip()}\n{user_agent.strip()[:240]}"
    return token_hash(normalized)


@dataclass(frozen=True)
class IssuedSession:
    token: str
    csrf_token: str
    expires_at: object


def issue_session(session: Session, role: str, user_agent: str = "") -> IssuedSession:
    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    now = utc_now()
    expires_at = now + SESSION_TTL
    session.add(
        PanelSession(
            token_hash=token_hash(token),
            csrf_token_hash=token_hash(csrf_token),
            role=role,
            created_at=now,
            expires_at=expires_at,
            last_seen_at=now,
            user_agent=str(user_agent or "")[:240],
        )
    )
    active = session.scalars(
        select(PanelSession)
        .where(PanelSession.revoked_at.is_(None), PanelSession.expires_at > now)
        .order_by(PanelSession.last_seen_at.desc(), PanelSession.created_at.desc())
    ).all()
    for stale in active[MAX_ACTIVE_SESSIONS - 1 :]:
        stale.revoked_at = now
    session.commit()
    return IssuedSession(token=token, csrf_token=csrf_token, expires_at=expires_at)


def find_session(session: Session, token: str) -> PanelSession | None:
    if not token:
        return None
    row = session.get(PanelSession, token_hash(token))
    now = utc_now()
    if not row or row.revoked_at is not None or row.expires_at <= now:
        return None
    if row.last_seen_at is None or now - row.last_seen_at >= SESSION_ACTIVITY_INTERVAL:
        row.last_seen_at = now
        session.commit()
    return row


def csrf_is_valid(row: PanelSession | None, provided_token: str) -> bool:
    if row is None or not provided_token:
        return False
    return secrets.compare_digest(row.csrf_token_hash, token_hash(provided_token))


def revoke_session(session: Session, token: str) -> None:
    row = session.get(PanelSession, token_hash(token)) if token else None
    if row and row.revoked_at is None:
        row.revoked_at = utc_now()
        session.commit()


def revoke_other_sessions(session: Session, preserved_token: str = "") -> None:
    preserved_hash = token_hash(preserved_token) if preserved_token else ""
    now = utc_now()
    rows = session.scalars(select(PanelSession).where(PanelSession.revoked_at.is_(None))).all()
    for row in rows:
        if row.token_hash != preserved_hash:
            row.revoked_at = now
    session.flush()


def clear_expired_sessions(session: Session) -> int:
    result = session.execute(delete(PanelSession).where(PanelSession.expires_at <= utc_now()))
    return int(result.rowcount or 0)


def login_retry_after(session: Session, fingerprint: str) -> int:
    row = session.get(PanelLoginAttempt, fingerprint)
    if not row or row.blocked_until is None:
        return 0
    seconds = int((row.blocked_until - utc_now()).total_seconds())
    return max(0, seconds + (1 if seconds >= 0 else 0))


def record_login_failure(session: Session, fingerprint: str) -> int:
    now = utc_now()
    row = session.get(PanelLoginAttempt, fingerprint)
    if not row:
        row = PanelLoginAttempt(fingerprint=fingerprint, failures=0)
        session.add(row)
    row.failures = min(int(row.failures or 0) + 1, 20)
    delay = min(60, 2 ** min(row.failures - 1, 6))
    row.last_failed_at = now
    row.blocked_until = now + timedelta(seconds=delay)
    session.commit()
    return delay


def clear_login_failures(session: Session, fingerprint: str) -> None:
    row = session.get(PanelLoginAttempt, fingerprint)
    if row:
        session.delete(row)
        session.commit()
