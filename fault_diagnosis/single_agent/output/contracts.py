"""Task-level output contracts for deterministic final-answer rendering."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..workflow.contracts import TaskType


MissingEvidencePolicy = Literal[
    "disclose_and_downgrade",
    "ask_for_more_info",
    "answer_with_known_limits",
]
OutputTone = Literal["brief", "diagnostic", "formal_report", "safety_boundary"]


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
