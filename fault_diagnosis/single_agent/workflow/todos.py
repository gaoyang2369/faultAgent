"""Task-list projection for workflow execution progress."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..contracts import SingleAgentDecision

WORKFLOW_STAGE_ORDER = [
    "understand",
    "select_workflow_policy",
    "initialize_evidence_bundle",
    "permission_check",
    "risk_check",
    "sql",
    "knowledge",
    "analysis",
    "resolution_recommendation",
    "workorder_decision",
    "report",
    "evidence_validation",
    "final_answer",
    "output_guardrail",
    "audit_log",
    "save_artifact",
]


@dataclass(frozen=True)
class WorkflowTodoGroup:
    """High-level workflow phase shown in the product task panel."""

    group_id: str
    title: str
    description: str
    stages: tuple[str, ...]


WORKFLOW_TODO_GROUPS = (
    WorkflowTodoGroup(
        group_id="plan",
        title="理解与规划",
        description="识别任务类型、执行路径和证据要求",
        stages=("understand", "select_workflow_policy", "initialize_evidence_bundle"),
    ),
    WorkflowTodoGroup(
        group_id="evidence",
        title="收集证据",
        description="查询运行数据、知识库，并完成必要的权限与风险检查",
        stages=("permission_check", "risk_check", "sql", "knowledge"),
    ),
    WorkflowTodoGroup(
        group_id="analysis",
        title="诊断分析",
        description="基于证据形成结论、候选原因和处置建议",
        stages=("analysis", "resolution_recommendation", "workorder_decision"),
    ),
    WorkflowTodoGroup(
        group_id="report",
        title="生成报告",
        description="生成诊断报告或 RCA 产物",
        stages=("report",),
    ),
    WorkflowTodoGroup(
        group_id="validation",
        title="校验并完成",
        description="校验证据一致性，整理回复并保存产物",
        stages=(
            "evidence_validation",
            "final_answer",
            "output_guardrail",
            "audit_log",
            "save_artifact",
        ),
    ),
)


def workflow_stage_sequence(decision: SingleAgentDecision | None) -> list[str]:
    """Return the enabled internal stage sequence for a workflow decision."""

    if decision is None:
        return []
    enabled_nodes = decision.enabled_nodes or {}
    always_visible = {
        "understand",
        "select_workflow_policy",
        "initialize_evidence_bundle",
        "analysis",
        "evidence_validation",
        "final_answer",
        "output_guardrail",
        "save_artifact",
    }
    sequence: list[str] = []
    for stage in WORKFLOW_STAGE_ORDER:
        if stage in always_visible or enabled_nodes.get(stage):
            sequence.append(stage)
    return sequence


def build_workflow_todos(
    decision: SingleAgentDecision | None,
    *,
    completed_stages: set[str] | None = None,
    skipped_stages: set[str] | None = None,
    current_stage: str | None = None,
) -> list[dict[str, Any]]:
    """Project internal workflow progress into a concise frontend todo shape."""

    completed = completed_stages or set()
    skipped = skipped_stages or set()
    sequence = workflow_stage_sequence(decision)
    if not sequence:
        return []

    active_stage = current_stage if current_stage in sequence else None
    if active_stage is None:
        active_stage = next(
            (stage for stage in sequence if stage not in completed and stage not in skipped),
            None,
        )

    todos: list[dict[str, Any]] = []
    for group in WORKFLOW_TODO_GROUPS:
        active_stages = [stage for stage in group.stages if stage in sequence]
        if not active_stages:
            continue

        if all(stage in completed or stage in skipped for stage in active_stages):
            status = "completed"
        elif active_stage in active_stages:
            status = "in_progress"
        else:
            status = "pending"

        description = _group_description(group, active_stages, skipped)
        todos.append(
            {
                "id": f"wf_group_{group.group_id}",
                "title": f"{len(todos) + 1}. {group.title}",
                "description": description,
                "status": status,
                "stage": group.group_id,
                "stages": active_stages,
            }
        )
    return todos


def _group_description(
    group: WorkflowTodoGroup,
    active_stages: list[str],
    skipped: set[str],
) -> str:
    if group.group_id == "evidence" and not {"sql", "knowledge"}.intersection(active_stages):
        description = "确认权限边界、审批要求和风险等级"
    else:
        description = group.description

    if skipped.intersection(active_stages):
        return f"{description}（部分节点已跳过）"
    return description


def summarize_workflow_todos(todos: list[dict[str, Any]]) -> dict[str, int]:
    """Return task summary compatible with the existing task panel."""

    return {
        "total": len(todos),
        "pending": sum(1 for item in todos if item.get("status") == "pending"),
        "in_progress": sum(1 for item in todos if item.get("status") == "in_progress"),
        "completed": sum(1 for item in todos if item.get("status") == "completed"),
        "interrupted": sum(1 for item in todos if item.get("status") == "interrupted"),
    }
