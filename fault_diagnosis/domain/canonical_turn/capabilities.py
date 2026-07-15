"""Canonical capability registry shared by semantic resolution and planning."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CapabilitySpec:
    capability: str
    required_slots: tuple[str, ...] = ()
    optional_slots: tuple[str, ...] = ()
    allowed_source_types: tuple[str, ...] = ()
    runtime_nodes: tuple[str, ...] = ()
    risk_level: str = "medium"
    user_visible: bool = True
    llm_may_propose: bool = True
    approval_required: bool = False
    deliverables: tuple[str, ...] = ()


CAPABILITY_SPECS: dict[str, CapabilitySpec] = {
    "check_runtime_status": CapabilitySpec(
        capability="check_runtime_status", required_slots=("device",), optional_slots=("time_window",),
        allowed_source_types=("sql_artifact",), runtime_nodes=("sql",), deliverables=("runtime_status",),
    ),
    "compare_runtime_status": CapabilitySpec(
        capability="compare_runtime_status", required_slots=("device",), optional_slots=("time_window",),
        allowed_source_types=("sql_artifact",), runtime_nodes=("sql", "comparison"), deliverables=("runtime_comparison",),
    ),
    "diagnose_fault": CapabilitySpec(
        capability="diagnose_fault", required_slots=("device",), optional_slots=("time_window", "fault_code"),
        allowed_source_types=("sql_artifact", "analysis_artifact"), runtime_nodes=("sql", "rag", "analysis"),
        deliverables=("diagnosis",),
    ),
    "explain_fault_code": CapabilitySpec(
        capability="explain_fault_code", required_slots=("fault_code",), runtime_nodes=("rag",),
        risk_level="low", deliverables=("fault_code_explanation",),
    ),
    "resolution_recommendation": CapabilitySpec(
        capability="resolution_recommendation", required_slots=("device",), optional_slots=("fault_code",),
        allowed_source_types=("analysis_artifact",), runtime_nodes=("analysis",), deliverables=("recommendations",),
    ),
    "generate_report": CapabilitySpec(
        capability="generate_report", optional_slots=("device",),
        allowed_source_types=("analysis_artifact", "report_artifact"), runtime_nodes=("report",),
        risk_level="low", deliverables=("report",),
    ),
    "evaluate_workorder_need": CapabilitySpec(
        capability="evaluate_workorder_need", required_slots=("device",), optional_slots=("fault_code",),
        allowed_source_types=("analysis_artifact",), runtime_nodes=("analysis", "workorder"),
        deliverables=("workorder_need_assessment",),
    ),
    "create_workorder_draft": CapabilitySpec(
        capability="create_workorder_draft", required_slots=("device",), optional_slots=("fault_code",),
        allowed_source_types=("analysis_artifact", "report_artifact"), runtime_nodes=("analysis", "workorder", "approval"),
        risk_level="high", approval_required=True, deliverables=("workorder_draft",),
    ),
    "dispatch_workorder": CapabilitySpec(
        capability="dispatch_workorder", allowed_source_types=("workorder_artifact",), runtime_nodes=("approval",),
        risk_level="high", approval_required=True,
    ),
}


def capability_spec(capability: str) -> CapabilitySpec | None:
    return CAPABILITY_SPECS.get(capability)


CANONICAL_CAPABILITIES = frozenset(CAPABILITY_SPECS)
