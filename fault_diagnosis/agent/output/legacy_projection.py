"""One-way deprecated output aliases for persisted SSE compatibility."""

from __future__ import annotations

from typing import Any

from ..contracts import CompositeOutputFrame, DeliverableResult, LegacyDeliverableProjection


_DELIVERABLE_TYPE_BY_CAPABILITY = {
    "explain_fault_code": "fault_code_explanation",
    "check_runtime_status": "runtime_status",
    "compare_runtime_status": "runtime_comparison",
    "diagnose_fault": "diagnosis",
    "resolution_recommendation": "recommendations",
    "generate_report": "report",
    "create_workorder_draft": "workorder_draft",
    "dispatch_workorder": "permission_denied",
    "evaluate_workorder_need": "workorder_draft",
}


def legacy_deliverable_type(capability: str) -> str:
    """Return the deprecated public label without influencing canonical decisions."""

    return _DELIVERABLE_TYPE_BY_CAPABILITY.get(capability, "clarification")


def project_legacy_deliverable(item: DeliverableResult) -> LegacyDeliverableProjection:
    """Project canonical content to old aliases; legacy values are never read back."""

    return LegacyDeliverableProjection(
        deliverable_type=legacy_deliverable_type(item.capability),
        payload=dict(item.structured_content),
        source_artifact_ids=list(item.artifact_ids),
    )


def serialize_composite_output(frame: CompositeOutputFrame) -> dict[str, Any]:
    """Serialize canonical output with deprecated aliases at the transport boundary."""

    payload = frame.model_dump(mode="json", exclude_none=True)
    payload["deliverables"] = [
        {
            **item.model_dump(mode="json", exclude_none=True),
            **project_legacy_deliverable(item).model_dump(mode="json"),
        }
        for item in frame.deliverables
    ]
    return payload
