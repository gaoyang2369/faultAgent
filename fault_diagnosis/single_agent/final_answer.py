"""Final user-answer formatting for the restricted single-agent runtime."""

from __future__ import annotations

from ..diagnosis.contracts import (
    AnalysisStepArtifact,
    EvidenceBundle,
    KnowledgeStepArtifact,
    ReportStepArtifact,
    SqlStepArtifact,
    WorkOrderSuggestion,
)
from .contracts import SingleAgentDecision
from .output.contracts import RenderedAnswer
from .output.renderers import render_final_answer


def build_templated_final_answer(
    *,
    decision: SingleAgentDecision,
    evidence_bundle: EvidenceBundle | None,
    analysis_artifact: AnalysisStepArtifact,
    workorder_suggestion: WorkOrderSuggestion | None = None,
    report_artifact: ReportStepArtifact | None = None,
    sql_artifact: SqlStepArtifact | None = None,
    knowledge_artifact: KnowledgeStepArtifact | None = None,
) -> RenderedAnswer:
    """Build a deterministic task-template answer.

    ``final_content`` remains the rendered ``content`` string for SSE and
    frontend compatibility; section metadata is kept alongside it for trace,
    payload and output guardrail checks.
    """

    return render_final_answer(
        decision=decision,
        evidence_bundle=evidence_bundle,
        analysis_artifact=analysis_artifact,
        workorder_suggestion=workorder_suggestion,
        report_artifact=report_artifact,
        sql_artifact=sql_artifact,
        knowledge_artifact=knowledge_artifact,
    )
