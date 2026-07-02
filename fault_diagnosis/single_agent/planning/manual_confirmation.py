"""Manual confirmation contract for high-risk workorder/action paths."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .action_readiness import classify_action_type

MANUAL_CONFIRMATION_SCHEMA_VERSION = "manual_confirmation_requirement.v1"

ConfirmationType = Literal["workorder_draft", "dispatch", "reset", "stop_machine", "parameter_change", "unknown"]
RequiredRole = Literal["engineer", "admin", "unknown"]
AllowedNextStep = Literal["draft_only", "ask_confirmation", "deny", "refresh_data_first"]

FORBIDDEN_EXECUTION_PHRASES = [
    "已派发",
    "已执行",
    "已复位",
    "已停机",
    "已修改参数",
    "dispatched",
    "executed",
    "reset done",
    "machine stopped",
    "parameter changed",
]


class ManualConfirmationRequirement(BaseModel):
    schema_version: str = MANUAL_CONFIRMATION_SCHEMA_VERSION
    required: bool = False
    reason: str = ""
    confirmation_type: ConfirmationType = "unknown"
    required_role: RequiredRole = "unknown"
    allowed_next_step: AllowedNextStep = "deny"
    forbidden_phrases: list[str] = Field(default_factory=lambda: list(FORBIDDEN_EXECUTION_PHRASES))


def build_manual_confirmation_requirement(
    *,
    decision: Any,
    workorder_action_readiness: Any | None = None,
) -> ManualConfirmationRequirement:
    """Return the compact human-confirmation contract for the current decision."""

    readiness = _to_dict(workorder_action_readiness)
    action_type = str(readiness.get("action_type") or classify_action_type(decision))
    if action_type == "unknown":
        return ManualConfirmationRequirement(
            required=False,
            reason="not_workorder_or_action",
            confirmation_type="unknown",
            required_role="unknown",
            allowed_next_step="deny",
        )

    confirmation_type = _confirmation_type(decision, action_type)
    stale_refresh_required = bool(readiness.get("stale_refresh_required", False))
    blockers = set(_strings(readiness.get("blockers")))
    if stale_refresh_required or "stale_refresh_or_disclosure_required" in blockers:
        next_step: AllowedNextStep = "refresh_data_first"
    elif action_type == "workorder_draft":
        next_step = "draft_only"
    elif action_type == "workorder_decision":
        next_step = "ask_confirmation"
    elif action_type == "device_action":
        next_step = "deny"
    else:
        next_step = "deny"

    return ManualConfirmationRequirement(
        required=True,
        reason=f"{action_type}_requires_human_confirmation",
        confirmation_type=confirmation_type,
        required_role=_required_role(confirmation_type),
        allowed_next_step=next_step,
    )


def summarize_manual_confirmation_requirement(value: Any) -> dict[str, Any]:
    data = value.model_dump(exclude_none=True) if isinstance(value, ManualConfirmationRequirement) else _to_dict(value)
    if not data:
        return {}
    return {
        "required": bool(data.get("required", False)),
        "reason": data.get("reason", ""),
        "confirmation_type": data.get("confirmation_type", "unknown"),
        "required_role": data.get("required_role", "unknown"),
        "allowed_next_step": data.get("allowed_next_step", "deny"),
        "forbidden_phrases": list(data.get("forbidden_phrases") or []),
    }


def contains_forbidden_execution_phrase(text: str) -> bool:
    normalized = str(text or "").lower()
    return any(phrase.lower() in normalized for phrase in FORBIDDEN_EXECUTION_PHRASES)


def _confirmation_type(decision: Any, action_type: str) -> ConfirmationType:
    text = " ".join(
        _strings(
            [
                getattr(decision, "action_type", "") or "",
                getattr(decision, "action_target", "") or "",
                getattr(decision, "user_goal", "") or "",
                str(getattr(decision, "goal_summary", "") or ""),
            ]
        )
    )
    if any(word in text for word in ("dispatch", "派发", "下发")):
        return "dispatch"
    if action_type == "workorder_draft":
        return "workorder_draft"
    if action_type == "workorder_decision":
        return "workorder_draft"
    if any(word in text for word in ("reset", "restart", "复位", "重启")):
        return "reset"
    if any(word in text for word in ("stop", "shutdown", "停机", "关闭")):
        return "stop_machine"
    if any(word in text for word in ("parameter", "config", "参数", "配置", "修改")):
        return "parameter_change"
    return "unknown"


def _required_role(confirmation_type: ConfirmationType) -> RequiredRole:
    if confirmation_type == "workorder_draft":
        return "engineer"
    if confirmation_type in {"dispatch", "reset", "stop_machine", "parameter_change"}:
        return "admin"
    return "unknown"


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def _to_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    if isinstance(value, dict):
        return value
    return {}
