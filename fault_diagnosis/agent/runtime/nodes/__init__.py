"""Real typed node registry for Agent Engine V2 runtime."""

from __future__ import annotations

from typing import Any

from ..tool_runtime import ToolRuntime
from .analysis import AnalysisNode
from .approval import ApprovalNode
from .kg import KgNode
from .rag import RagNode
from .report import ReportNode
from .sql import SqlNode
from .workorder import WorkorderNode
from .clarification import ClarificationNode


def build_real_node_registry(*, tool_runtime: ToolRuntime) -> dict[str, Any]:
    return {
        "sql": SqlNode(tool_runtime),
        "rag": RagNode(tool_runtime),
        "kg": KgNode(),
        "analysis": AnalysisNode(),
        "report": ReportNode(tool_runtime),
        "workorder": WorkorderNode(),
        "approval": ApprovalNode(),
        "clarification": ClarificationNode(),
    }


__all__ = [
    "AnalysisNode",
    "ApprovalNode",
    "KgNode",
    "RagNode",
    "ReportNode",
    "SqlNode",
    "WorkorderNode",
    "ClarificationNode",
    "build_real_node_registry",
]
