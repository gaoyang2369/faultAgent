"""把模型 JSON 适配为 Wave 2 的非权威意图提议合同。"""

from __future__ import annotations

from typing import Any

from fault_diagnosis.agent.semantics.contracts import (
    SemanticClauseProposal,
    SemanticTurnProposal,
)


def parse_semantic_turn_proposal(payload: dict[str, Any]) -> SemanticTurnProposal:
    """接受新合同，并兼容 Wave 1 的 ``model_clause_parse.v1`` 输出。"""

    if payload.get("schema_version") == "semantic_turn_proposal.v1":
        return SemanticTurnProposal.model_validate(payload)
    if payload.get("schema_version") != "model_clause_parse.v1":
        raise ValueError("unsupported_semantic_schema")
    clauses: list[SemanticClauseProposal] = []
    for item in payload.get("clauses", []):
        action = item.get("action") or {}
        metadata = item.get("shadow_metadata") or {}
        clauses.append(SemanticClauseProposal(
            clause_index=item["clause_index"], text=item["text"], start=item["start"], end=item["end"],
            capability=action.get("capability"), confidence=action.get("confidence", 0.0),
            requested=metadata.get("requested", True), negated=metadata.get("negated", False),
            conditional=metadata.get("conditional", False), sequence_index=metadata.get("sequence_index"),
            depends_on_clause_indexes=metadata.get("depends_on", []),
            source_kind=(item.get("source") or {}).get("source_kind", "current_message"),
        ))
    return SemanticTurnProposal(schema_version="semantic_turn_proposal.v1", clauses=clauses)
