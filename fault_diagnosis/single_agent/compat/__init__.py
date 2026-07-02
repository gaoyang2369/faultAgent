"""Compatibility helpers for deprecated single-agent legacy fields."""

from .legacy_intent import (
    build_legacy_intent_stack,
    explain_legacy_field_usage,
    project_task_type_for_compat,
)

__all__ = [
    "build_legacy_intent_stack",
    "explain_legacy_field_usage",
    "project_task_type_for_compat",
]
