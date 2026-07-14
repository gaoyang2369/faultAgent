"""Agent Engine V2 output and compatibility projections."""

from .answer import build_output_frame
from .deliverables import DeliverableAssembler
from .presenter import CompositePresenter, PresentedOutput
from .legacy_projection import project_legacy_deliverable, serialize_composite_output
from .artifact_projection import project_artifact_envelope
from .artifact_manifest import build_artifact_manifests, latest_focus_from_manifests
from .answer_contracts import AnswerSourcePacket, GroundedAnswerModelOutput, GroundedAnswerResult
from .answer_projection import effective_answer_frame, project_answer_complete_payload
from .answer_source import build_answer_source_packet
from .answer_source_budget import compact_answer_source_packet
from .answer_validator import GroundedAnswerValidator
from .grounded_answer import GroundedAnswerSynthesizer
from .report import build_reportable_payload
from .sse_projection import (
    project_complete,
    project_start,
    project_task_update,
    project_token,
    project_tool_end,
    project_tool_start,
)

__all__ = [
    "build_output_frame",
    "AnswerSourcePacket",
    "CompositePresenter",
    "DeliverableAssembler",
    "PresentedOutput",
    "GroundedAnswerModelOutput",
    "GroundedAnswerResult",
    "GroundedAnswerSynthesizer",
    "GroundedAnswerValidator",
    "build_answer_source_packet",
    "compact_answer_source_packet",
    "effective_answer_frame",
    "project_answer_complete_payload",
    "project_legacy_deliverable",
    "serialize_composite_output",
    "build_artifact_manifests",
    "build_reportable_payload",
    "latest_focus_from_manifests",
    "project_artifact_envelope",
    "project_complete",
    "project_start",
    "project_task_update",
    "project_token",
    "project_tool_end",
    "project_tool_start",
]
