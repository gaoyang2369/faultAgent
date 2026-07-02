"""Deterministic workflow node helpers."""

from __future__ import annotations

from typing import Any

from ..contracts import SingleAgentDecision
from ...diagnosis.contracts import AnalysisStepArtifact
from .axes import goal_types, requests_action_or_workorder


def workflow_node_enabled(decision: SingleAgentDecision, node_name: str) -> bool:
    """Return whether a resolved workflow node should run."""

    return bool((decision.enabled_nodes or {}).get(node_name, False))


def build_permission_check_result(decision: SingleAgentDecision, *, user_identity: str) -> dict[str, Any]:
    """Build a conservative permission result for write/action intents."""

    action_type = _action_type(decision)
    return {
        "node": "permission_check",
        "allowed": False,
        "decision": "draft_or_confirmation_only",
        "action_type": action_type,
        "user_identity": user_identity,
        "requires_human_confirmation": True,
        "reason": "当前 Agent 不直接执行设备控制、配置修改、告警关闭或工单派发，只能生成建议、草稿或审批提示。",
        "forbidden_tools": (decision.workflow_policy or {}).get("forbidden_tools", []),
    }


def build_risk_check_result(decision: SingleAgentDecision) -> dict[str, Any]:
    """Build a deterministic risk result from the routed action."""

    risk_level = decision.risk_level or "read_only"
    high_risk = risk_level in {"high_risk", "write_action"}
    return {
        "node": "risk_check",
        "risk_level": risk_level,
        "requires_human_confirmation": high_risk or requests_action_or_workorder(decision),
        "decision": "deny_direct_execution" if high_risk else "require_confirmation",
        "reason": (
            "该请求涉及高风险写操作，必须转人工确认或审批。"
            if high_risk
            else "该请求涉及状态变更意图，执行前仍需人工确认。"
        ),
        "guardrails": decision.guardrails,
    }


def build_resolution_recommendation_result(
    *,
    decision: SingleAgentDecision,
    analysis_artifact: AnalysisStepArtifact,
) -> dict[str, Any]:
    """Expose analysis recommendations as a first-class workflow node artifact."""

    return {
        "node": "resolution_recommendation",
        "task_family": decision.task_family,
        "goal_types": goal_types(decision),
        "recommendations": _clean_items(analysis_artifact.recommendations),
        "verification_items": _clean_items(analysis_artifact.verification_items),
        "risk_notice": analysis_artifact.risk_notice,
        "blocked_subgoals": [
            item for item in decision.subgoals if item.get("status") == "blocked"
        ],
        "policy_guardrails": decision.guardrails,
    }


def build_audit_log_result(
    *,
    decision: SingleAgentDecision,
    permission_check: dict[str, Any],
    risk_check: dict[str, Any],
    output_guardrail: dict[str, Any],
) -> dict[str, Any]:
    """Build a structured audit record for action/write-intent workflows."""

    return {
        "node": "audit_log",
        "task_family": decision.task_family,
        "goal_types": goal_types(decision),
        "action_type": decision.action_type,
        "risk_level": decision.risk_level,
        "permission_decision": permission_check.get("decision"),
        "risk_decision": risk_check.get("decision"),
        "output_guardrail_passed": output_guardrail.get("passed"),
        "requires_human_confirmation": bool(
            permission_check.get("requires_human_confirmation")
            or risk_check.get("requires_human_confirmation")
        ),
        "policy_id": (decision.workflow_policy or {}).get("policy_id"),
        "forbidden_tools": (decision.workflow_policy or {}).get("forbidden_tools", []),
    }


def _action_type(decision: SingleAgentDecision) -> str:
    if decision.action_type:
        return decision.action_type
    if decision.flags.get("may_involve_write_action"):
        return "write_action"
    return "read_only"


def _clean_items(items: list[str]) -> list[str]:
    cleaned = [str(item).strip() for item in items if str(item or "").strip()]
    return list(dict.fromkeys(cleaned))
