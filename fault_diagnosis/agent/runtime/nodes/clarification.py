"""Deterministic clarification runtime node."""

from __future__ import annotations

from typing import Any

from ..executor import NodeExecutionOutput
from ..state import RuntimeState
from .base import input_value


class ClarificationNode:
    node_type = "clarification"

    def run(self, *, node: dict[str, Any], state: RuntimeState) -> NodeExecutionOutput:  # noqa: ARG002
        missing_slots = list(input_value(node, "missing_slots", []) or [])
        candidate_targets = list(input_value(node, "candidate_targets", []) or [])
        question = str(input_value(node, "clarification_question", "") or "").strip()
        if not question:
            question = _question_for_slots(missing_slots)
        return NodeExecutionOutput(
            output={
                "clarification_question": question,
                "missing_slots": missing_slots,
                "candidate_targets": candidate_targets,
                "reason": str(input_value(node, "reason", "authorized_but_incomplete") or "authorized_but_incomplete"),
            }
        )


def _question_for_slots(missing_slots: list[str]) -> str:
    if any("device" in item for item in missing_slots):
        return "请确认要查询或处理的设备。"
    if any("fault" in item for item in missing_slots):
        return "请确认要处理的故障码。"
    return "请补充要处理的设备、故障码或目标结果。"
