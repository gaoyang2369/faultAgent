"""校验模型 JSON 为唯一的非权威语义提议合同。"""

from __future__ import annotations

from typing import Any

from fault_diagnosis.agent.semantics.contracts import SemanticTurnProposal


def parse_semantic_turn_proposal(payload: dict[str, Any]) -> SemanticTurnProposal:
    """只接受统一异步入口输出的 ``semantic_turn_proposal.v1``。"""

    if payload.get("schema_version") != "semantic_turn_proposal.v1":
        raise ValueError("unsupported_semantic_schema")
    return SemanticTurnProposal.model_validate(payload)
