"""Public response projection for a validated grounded answer."""

from __future__ import annotations

from typing import Any

from ..contracts import OutputFrame
from .answer_contracts import GroundedAnswerResult


def effective_answer_frame(
    deterministic_frame: OutputFrame,
    answer_result: GroundedAnswerResult,
) -> OutputFrame:
    """Create a presentation-only copy; never mutate the runtime output authority."""

    effective = answer_result.answer if answer_result.status == "generated" else deterministic_frame.final_answer
    return deterministic_frame.model_copy(update={"final_answer": effective}, deep=True)


def project_answer_complete_payload(
    complete_payload: dict[str, Any],
    *,
    deterministic_answer: str,
    answer_result: GroundedAnswerResult,
) -> dict[str, Any]:
    """Attach effective/raw content and a small audit summary to chat_complete."""

    projected = dict(complete_payload)
    effective = answer_result.answer if answer_result.status == "generated" else deterministic_answer
    projected.update(
        {
            "final_content": effective,
            "content": effective,
            "raw_final_content": deterministic_answer,
            "grounded_final_content": effective if answer_result.status == "generated" else "",
            "final_answer_source": answer_result.final_answer_source,
            "answer_synthesis": answer_result.complete_summary(),
        }
    )
    rendered = projected.get("rendered_answer")
    if isinstance(rendered, dict):
        projected["rendered_answer"] = {**rendered, "final_answer": effective}
    return projected
