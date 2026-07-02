"""Task-level output contracts for deterministic final-answer rendering."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


MissingEvidencePolicy = Literal[
    "disclose_and_downgrade",
    "ask_for_more_info",
    "answer_with_known_limits",
]
OutputTone = Literal["brief", "diagnostic", "formal_report", "safety_boundary"]


class TaskType(str, Enum):
    """Deprecated task labels retained only for output-template compatibility."""

    STATUS_QUERY = "status_query"
    ALARM_TRIAGE = "alarm_triage"
    FAULT_DIAGNOSIS = "fault_diagnosis"
    ROOT_CAUSE_ANALYSIS = "root_cause_analysis"
    HEALTH_ASSESSMENT = "health_assessment"
    KNOWLEDGE_QA = "knowledge_qa"
    REPORT_GENERATION = "report_generation"
    ACTION_REQUEST = "action_request"
    PERMISSION_SCOPE_QUERY = "permission_scope_query"


class OutputSectionContract(BaseModel):
    """Contract for one rendered answer section."""

    key: str
    title: str
    required: bool = True
    require_evidence: bool = False
    allow_empty: bool = False
    fallback_when_missing: str | None = None


class OutputContract(BaseModel):
    """Template contract selected by the routed task type."""

    task_type: TaskType
    template_id: str
    description: str = ""
    sections: list[OutputSectionContract]
    require_evidence_ids: bool = True
    allow_workorder_suggestion: bool = False
    allow_report_link: bool = False
    missing_evidence_policy: MissingEvidencePolicy = "disclose_and_downgrade"
    tone: OutputTone = "diagnostic"
    max_bullets_per_section: int = 5
    max_chars: int | None = None


class RenderedSection(BaseModel):
    """One deterministic section in the rendered answer."""

    key: str
    title: str
    content: str
    evidence_ids: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)


class RenderedAnswer(BaseModel):
    """Rendered final answer plus traceable section metadata."""

    task_type: TaskType
    template_id: str
    content: str
    sections: list[RenderedSection]
    used_evidence_ids: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    guardrail_notes: list[str] = Field(default_factory=list)
