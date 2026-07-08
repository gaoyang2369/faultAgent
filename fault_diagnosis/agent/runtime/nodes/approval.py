"""Approval boundary runtime node for Agent Engine V2."""

from __future__ import annotations

from typing import Any

from ..executor import NodeExecutionOutput
from ..state import RuntimeState
from .base import input_value


class ApprovalNode:
    node_type = "approval"

    def run(self, *, node: dict[str, Any], state: RuntimeState) -> NodeExecutionOutput:
        requirements = _requirements(node, state)
        if not requirements:
            return NodeExecutionOutput(output={"required": False, "approval_requirements": []})

        normalized = [_normalize_requirement(item) for item in requirements]
        deny = any(item.get("allowed_next_step") == "deny" or _is_dangerous(item) for item in normalized)
        interrupt = {
            "interrupt_id": str(input_value(node, "interrupt_id", "") or f"interrupt_{node.get('node_id') or 'approval'}"),
            "type": "approval_required",
            "approval_requirements": normalized,
            "allowed_next_step": "deny" if deny else normalized[0].get("allowed_next_step", "ask_confirmation"),
        }
        return NodeExecutionOutput(
            status="blocked",
            output={"required": True, "approval_requirements": normalized, "interrupts": [interrupt]},
            error={
                "code": "approval_denied_boundary" if deny else "approval_required",
                "message": "Human approval is required before continuing.",
            },
        )


def _requirements(node: dict[str, Any], state: RuntimeState) -> list[dict[str, Any]]:
    raw = input_value(node, "approval_requirements", None)
    if raw is None:
        raw = state.plan.approval_requirements
    if isinstance(raw, dict):
        return [raw]
    return [dict(item) for item in raw or [] if isinstance(item, dict)]


def _normalize_requirement(item: dict[str, Any]) -> dict[str, Any]:
    requirement = dict(item)
    req_type = str(requirement.get("type") or requirement.get("approval_type") or "approval")
    requirement.setdefault("type", req_type)
    requirement.setdefault("required", True)
    if req_type in {"workorder", "workorder.create", "workorder_draft"}:
        requirement.setdefault("required_role", "engineer")
        requirement.setdefault("allowed_next_step", "draft_only")
    elif any(word in req_type for word in ("device", "config", "dispatch")):
        requirement.setdefault("required_role", "admin")
        requirement["allowed_next_step"] = "deny"
    else:
        requirement.setdefault("required_role", "engineer")
        requirement.setdefault("allowed_next_step", "ask_confirmation")
    return requirement


def _is_dangerous(item: dict[str, Any]) -> bool:
    text = " ".join(str(value) for value in item.values()).casefold()
    return any(word in text for word in ("device_control.write", "config.write", "dispatch", "device_action"))
