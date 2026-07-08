"""Shared helpers for real Agent Engine V2 runtime nodes."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from fault_diagnosis.domain.diagnosis.contracts import DiagnosisRequest
from fault_diagnosis.domain.security.contracts import AuthContext
from fault_diagnosis.domain.security.permissions import build_auth_context
from ..state import RuntimeState


def node_inputs(node: dict[str, Any]) -> dict[str, Any]:
    value = node.get("inputs")
    return dict(value) if isinstance(value, dict) else {}


def input_value(node: dict[str, Any], name: str, default: Any = None) -> Any:
    inputs = node_inputs(node)
    return inputs.get(name, node.get(name, default))


def auth_context(state: RuntimeState) -> AuthContext:
    return state.auth_context or build_auth_context(role="guest")


def build_request(state: RuntimeState, node: dict[str, Any], *, goal: str = "V2 工具节点执行") -> DiagnosisRequest:
    inputs = node_inputs(node)
    goals = state.plan.goals or []
    first_goal = goals[0] if goals and isinstance(goals[0], dict) else {}
    raw_message = str(
        inputs.get("user_message")
        or inputs.get("query")
        or first_goal.get("description")
        or first_goal.get("goal")
        or goal
    )
    device_refs = inputs.get("device_refs") or inputs.get("devices") or first_goal.get("device_refs") or []
    if isinstance(device_refs, str):
        device_refs = [device_refs]
    fault_codes = inputs.get("fault_codes") or inputs.get("fault_code_refs") or first_goal.get("fault_code_refs") or []
    if isinstance(fault_codes, str):
        fault_codes = [fault_codes]
    auth = auth_context(state)
    return DiagnosisRequest(
        user_message=raw_message,
        user_identity=auth.display_name or auth.user_id or auth.role,
        equipment_hint=str(inputs.get("equipment_hint") or (device_refs[0] if device_refs else "") or "") or None,
        metric_hint=str(inputs.get("metric_hint") or "") or None,
        fault_code_hint=str(inputs.get("fault_code_hint") or (fault_codes[0] if fault_codes else "") or "") or None,
        time_range_hint=str(inputs.get("time_range_hint") or "") or None,
        needs_report=bool(inputs.get("needs_report", "report.write_draft" in state.plan.allowed_tools)),
        report_format=str(inputs.get("report_format") or "markdown"),
        analysis_goal=str(inputs.get("analysis_goal") or first_goal.get("goal") or goal),
    )


def build_decision_stub(node: dict[str, Any]) -> Any:
    inputs = node_inputs(node)
    devices = inputs.get("device_refs") or inputs.get("devices") or []
    if isinstance(devices, str):
        devices = [devices]
    return SimpleNamespace(
        objects={"device_ids": [str(item) for item in devices if str(item).strip()]},
        goal_set={"goals": []},
        action_target=inputs.get("action_target", ""),
        user_goal=inputs.get("user_message", ""),
        task_family=inputs.get("task_family", ""),
    )


def model_to_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    return dict(value) if isinstance(value, dict) else {}


def models_to_dicts(values: list[Any]) -> list[dict[str, Any]]:
    return [model_to_dict(item) for item in values]


def timestamp_for_filename() -> str:
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
