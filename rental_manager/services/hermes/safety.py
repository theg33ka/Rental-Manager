from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from rental_manager.services.owner_operations import OWNER_OPERATION_SPECS


READ_ONLY_TOOLS = {
    "read_case",
    "read_finance",
    "read_property",
    "read_contract",
    "read_messages",
    "build_briefing",
    "build_case_summary",
    "search_entities",
}

LEVEL_ONE_TOOLS = {
    "upsert_case",
    "close_resolved_case",
    "create_commitment",
    "schedule_case_review",
    "save_owner_preference",
    "create_message_draft",
    "send_approved_template_reminder",
    "select_approved_template",
    "notify_owner",
}

LEVEL_THREE_OPERATIONS = {
    "delete_lease",
    "delete_manual_debt",
    "delete_payment_receipt",
    "delete_utility_bill",
    "move_out",
    "transfer_lease",
    "database_import",
    "database_restore",
    "update_secrets",
}

LEVEL_TWO_OPERATIONS = set(OWNER_OPERATION_SPECS) - LEVEL_THREE_OPERATIONS


@dataclass(frozen=True)
class SafetyDecision:
    tool: str
    level: int
    confirmation_required: bool
    autonomous_allowed: bool
    reason: str


class ActionSafetyRegistry:
    def __init__(self, *, mass_action_threshold: int = 20) -> None:
        self.mass_action_threshold = max(1, mass_action_threshold)

    def classify(
        self,
        tool: str,
        payload: dict[str, Any] | None = None,
        *,
        owner_level_one_enabled: bool = False,
    ) -> SafetyDecision:
        payload = payload or {}
        normalized = str(tool or "").strip()
        target_count = self._target_count(payload)
        if normalized in READ_ONLY_TOOLS:
            return SafetyDecision(normalized, 0, False, True, "read-only operation")
        if normalized in LEVEL_ONE_TOOLS:
            return SafetyDecision(
                normalized,
                1,
                not owner_level_one_enabled,
                owner_level_one_enabled,
                "owner policy allows level-one internal automation" if owner_level_one_enabled else "owner policy is disabled",
            )
        if normalized in LEVEL_THREE_OPERATIONS or target_count > self.mass_action_threshold:
            reason = "critical or irreversible operation"
            if target_count > self.mass_action_threshold:
                reason = f"mass action contains {target_count} targets"
            return SafetyDecision(normalized, 3, True, False, reason)
        if normalized in LEVEL_TWO_OPERATIONS or normalized in {"grouped_owner_operation", "grouped_deferral"}:
            return SafetyDecision(normalized, 2, True, False, "operation changes business data")
        return SafetyDecision(normalized, 3, True, False, "tool is not registered")

    def validate_skill(self, skill: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        allowed_tools = self._as_string_list(skill.get("allowed_tools") or skill.get("allowed_tools_json"))
        autonomous_tools = self._as_string_list(
            skill.get("autonomous_tools") or skill.get("autonomous_tools_json")
        )
        confirmation_tools = self._as_string_list(
            skill.get("confirmation_required_tools") or skill.get("confirmation_required_tools_json")
        )
        unknown = [tool for tool in allowed_tools if self.classify(tool).reason == "tool is not registered"]
        if unknown:
            errors.append("Неизвестные tools: " + ", ".join(sorted(unknown)))
        for tool in autonomous_tools:
            decision = self.classify(tool, owner_level_one_enabled=True)
            if decision.level >= 2:
                errors.append(f"Tool {tool} нельзя выполнять автономно")
            if tool not in allowed_tools:
                errors.append(f"Автономный tool {tool} отсутствует в allowed_tools")
        for tool in allowed_tools:
            decision = self.classify(tool)
            if decision.level >= 2 and tool not in confirmation_tools:
                errors.append(f"Tool {tool} должен быть в confirmation_required_tools")
        return list(dict.fromkeys(errors))

    @staticmethod
    def _target_count(payload: dict[str, Any]) -> int:
        direct = payload.get("target_count")
        try:
            if direct is not None:
                return max(0, int(direct))
        except (TypeError, ValueError):
            pass
        for key in ("items", "operations", "lease_ids", "recipient_ids"):
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)
        return 1

    @staticmethod
    def _as_string_list(value: Any) -> list[str]:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                value = [value]
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]


ACTION_SAFETY_REGISTRY = ActionSafetyRegistry()
