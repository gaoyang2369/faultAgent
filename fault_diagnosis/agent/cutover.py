"""Deprecated V2 execution preparation imports.

Use fault_diagnosis.agent.runtime.plan_preparer instead.
"""

from __future__ import annotations

from .runtime.plan_preparer import V2ExecutionDecision, decide_v2_execution, prepare_v2_execution_plan

__all__ = ["V2ExecutionDecision", "decide_v2_execution", "prepare_v2_execution_plan"]
