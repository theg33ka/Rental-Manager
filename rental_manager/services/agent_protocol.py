from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any


ALLOWED_ACTION_TYPES = {
    "defer_rent",
    "move_out",
    "create_manual_debt",
    "send_tenant_message",
}
ALLOWED_MEMORY_KINDS = {"fact", "preference", "commitment", "decision", "warning"}
ALLOWED_TENANT_INTENTS = {
    "general",
    "payment_question",
    "payment_promise",
    "payment_sent",
    "deferral_request",
    "move_out_request",
    "complaint",
    "excuse",
}


@dataclass(frozen=True)
class AgentEnvelope:
    reply: str
    actions: list[dict[str, Any]] = field(default_factory=list)
    memories: list[dict[str, Any]] = field(default_factory=list)
    intent: str = "general"
    promise_date: str = ""
    needs_owner: bool = False
    owner_summary: str = ""


def _bounded_text(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit].rstrip()


def parse_json_object(raw: str) -> dict[str, Any] | None:
    value = (raw or "").strip()
    if not value:
        return None
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", value, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        value = fenced.group(1).strip()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        start = value.find("{")
        end = value.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(value[start : end + 1])
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def normalize_memories(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value[:5]:
        if not isinstance(item, dict):
            continue
        content = _bounded_text(item.get("content"), 500)
        if not content:
            continue
        kind = str(item.get("kind") or "fact").strip().lower()
        if kind not in ALLOWED_MEMORY_KINDS:
            kind = "fact"
        try:
            importance = min(3, max(1, int(item.get("importance") or 1)))
        except (TypeError, ValueError):
            importance = 1
        result.append({"kind": kind, "content": content, "importance": importance})
    return result


def normalize_actions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value[:3]:
        if not isinstance(item, dict):
            continue
        action_type = str(item.get("type") or item.get("action_type") or "").strip().lower()
        if action_type not in ALLOWED_ACTION_TYPES:
            continue
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {
            key: value
            for key, value in item.items()
            if key not in {"type", "action_type", "reason", "preview"}
        }
        result.append(
            {
                "type": action_type,
                "payload": payload,
                "reason": _bounded_text(item.get("reason"), 500),
            }
        )
    return result


def parse_agent_envelope(raw: str) -> AgentEnvelope:
    parsed = parse_json_object(raw)
    if parsed is None:
        return AgentEnvelope(reply=_bounded_text(raw, 3600))
    reply = _bounded_text(parsed.get("reply") or parsed.get("answer"), 3600)
    intent = str(parsed.get("intent") or "general").strip().lower()
    if intent not in ALLOWED_TENANT_INTENTS:
        intent = "general"
    return AgentEnvelope(
        reply=reply,
        actions=normalize_actions(parsed.get("actions")),
        memories=normalize_memories(parsed.get("memory") or parsed.get("memories")),
        intent=intent,
        promise_date=_bounded_text(parsed.get("promise_date"), 20),
        needs_owner=bool(parsed.get("needs_owner")),
        owner_summary=_bounded_text(parsed.get("owner_summary"), 1200),
    )


def action_confirmation_keyboard(proposal_id: int) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Подтвердить", "callback_data": f"agent:confirm:{proposal_id}"},
                {"text": "❌ Отклонить", "callback_data": f"agent:reject:{proposal_id}"},
            ]
        ]
    }
