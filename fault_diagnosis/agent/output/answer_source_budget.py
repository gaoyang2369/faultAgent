"""Priority-preserving character budget for compact AnswerFacts."""

from __future__ import annotations

from .answer_contracts import AnswerFacts


def compact_answer_source_packet(
    packet: AnswerFacts,
    *,
    max_chars: int,
) -> AnswerFacts | None:
    """Fit model-visible facts without removing result status, refs or safety metadata."""

    if _packet_chars(packet) <= max_chars:
        return packet
    compact = packet.model_copy(deep=True)
    compact.user_question = compact.user_question[:500]
    for text_limit, fact_limit, action_limit in ((240, 10, 6), (160, 8, 4), (100, 5, 3)):
        for result in compact.results:
            result.facts = [value[:text_limit] for value in result.facts[:fact_limit]]
            result.limitations = [value[:text_limit] for value in result.limitations[:4]]
            result.recommended_actions = [value[:text_limit] for value in result.recommended_actions[:action_limit]]
            result.failure_reason = result.failure_reason[:text_limit]
            result.data_basis = {
                key: value for key, value in result.data_basis.items()
                if key in {"resolution_mode", "resolution_modes", "latest_sample_time", "fallback_used"}
            }
        if _packet_chars(compact) <= max_chars:
            return compact
    return None


def _packet_chars(packet: AnswerFacts) -> int:
    return len(packet.model_dump_json(exclude_none=True))


__all__ = ["compact_answer_source_packet"]
