"""MCP adapters for shaping internal workflow outputs into stable protocol payloads."""

from .workflow import (
    build_artifact_items,
    build_diagnosis_findings,
    build_evidence_items,
    build_governance_info,
    build_resource_references,
    build_timeline_entries,
    extract_report_filename,
)

__all__ = [
    "build_artifact_items",
    "build_diagnosis_findings",
    "build_evidence_items",
    "build_governance_info",
    "build_resource_references",
    "build_timeline_entries",
    "extract_report_filename",
]
