"""校验模型 JSON 为唯一的非权威语义提议合同。"""

from __future__ import annotations

from typing import Any

from fault_diagnosis.agent.semantics.contracts import SemanticTurnProposal


def parse_semantic_turn_proposal(payload: dict[str, Any]) -> SemanticTurnProposal:
    """只接受统一异步入口输出的 ``semantic_turn_proposal.v1``。"""

    if payload.get("schema_version") != "semantic_turn_proposal.v1":
        raise ValueError("unsupported_semantic_schema")
    return SemanticTurnProposal.model_validate(_normalize_nullable_enums(payload))


def _normalize_nullable_enums(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize common local-model JSON spellings before strict validation."""

    normalized = dict(payload)
    clauses = []
    for raw in payload.get("clauses") or []:
        clause = dict(raw) if isinstance(raw, dict) else raw
        if isinstance(clause, dict) and _is_null_literal(clause.get("condition_type")):
            clause["condition_type"] = None
        clauses.append(clause)
    normalized["clauses"] = clauses
    context = payload.get("context")
    if isinstance(context, dict):
        context = dict(context)
        for field in ("temporal_relation", "ordinal"):
            if _is_null_literal(context.get(field)):
                context[field] = None
        normalized["context"] = context
    return normalized


def _is_null_literal(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip().lower() in {"", "none", "null"})
