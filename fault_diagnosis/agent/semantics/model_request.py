"""语义模型的唯一输入合同。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class SemanticModelRequest:
    """单轮模型调用输入，仅含当前消息与安全投影。"""

    text: str
    deterministic_entities: tuple[dict[str, Any], ...]
    deterministic_clauses: tuple[dict[str, Any], ...] = ()
    temperature: Literal[0] = 0
    response_schema: Literal["semantic_turn_proposal.v1"] = "semantic_turn_proposal.v1"
    schema_version: Literal["semantic_turn_request.v1"] = "semantic_turn_request.v1"
    allowed_capabilities: tuple[str, ...] = ()
    allowed_source_kinds: tuple[str, ...] = ("prior_result", "current_message")
    context_candidates: tuple[dict[str, Any], ...] = ()
    pending_summary: dict[str, Any] | None = None
