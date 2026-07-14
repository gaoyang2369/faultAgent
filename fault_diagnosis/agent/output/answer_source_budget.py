"""Priority-preserving character budget for answer source packets."""

from __future__ import annotations

from typing import Any

from .answer_contracts import AnswerSourcePacket


def compact_answer_source_packet(
    packet: AnswerSourcePacket,
    *,
    max_chars: int,
) -> AnswerSourcePacket | None:
    """Fit a packet structurally; never truncate serialized JSON."""

    if _packet_chars(packet) <= max_chars:
        return packet
    compact = packet.model_copy(deep=True)
    compact.deterministic_fallback = ""
    compact.user_request = compact.user_request[:1000]
    for deliverable in compact.deliverables:
        content = deliverable.get("structured_content")
        if isinstance(content, dict) and content.get("assessments"):
            content.pop("legacy_sql", None)
    supporting_ids = {
        str(evidence_id)
        for claim in compact.claims
        for evidence_id in claim.get("supporting_evidence_ids", [])
        if evidence_id
    }
    compact.evidence = [
        item for item in compact.evidence if str(item.get("evidence_id") or "") in supporting_ids
    ]
    for evidence in compact.evidence:
        evidence.pop("asset_id", None)
        evidence.pop("is_untrusted_data", None)
    for limit in (800, 400, 200):
        compact.deliverables = _limit_text(compact.deliverables, limit=limit)
        compact.claims = _limit_text(compact.claims, limit=limit)
        compact.evidence = _limit_text(compact.evidence, limit=limit)
        compact.limitations = [value[:limit] for value in compact.limitations]
        if _packet_chars(compact) <= max_chars:
            return compact
    return None


def _packet_chars(packet: AnswerSourcePacket) -> int:
    return len(packet.model_dump_json(exclude_none=True))


def _limit_text(value: Any, *, limit: int) -> Any:
    if isinstance(value, dict):
        return {key: _limit_text(child, limit=limit) for key, child in value.items()}
    if isinstance(value, list):
        return [_limit_text(child, limit=limit) for child in value]
    if isinstance(value, str):
        return value[:limit]
    return value
