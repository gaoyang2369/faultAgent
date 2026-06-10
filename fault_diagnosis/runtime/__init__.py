"""运行时能力聚合入口。"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_LAZY_EXPORTS = {
    "WORKFLOW_STAGE_ORDER": ("workflow_runtime", "WORKFLOW_STAGE_ORDER"),
    "ExecutionRuntimeContext": ("execution_runtime", "ExecutionRuntimeContext"),
    "activate_stage": ("workflow_runtime", "activate_stage"),
    "append_workflow_stage": ("workflow_runtime", "append_workflow_stage"),
    "build_diagnosis_runtime_payload": ("diagnosis_runtime", "build_diagnosis_runtime_payload"),
    "build_tool_end_payload": ("tool_runtime", "build_tool_end_payload"),
    "build_tool_start_payload": ("tool_runtime", "build_tool_start_payload"),
    "build_workflow_stage_details": ("workflow_runtime", "build_workflow_stage_details"),
    "complete_stage": ("workflow_runtime", "complete_stage"),
    "resolve_tool_stage": ("workflow_runtime", "resolve_tool_stage"),
    "touch_tool_stage_detail": ("tool_runtime", "touch_tool_stage_detail"),
    "upsert_stage_detail": ("workflow_runtime", "upsert_stage_detail"),
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> Any:
    if name not in _LAZY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = _LAZY_EXPORTS[name]
    value = getattr(import_module(f"{__name__}.{module_name}"), attribute_name)
    globals()[name] = value
    return value
