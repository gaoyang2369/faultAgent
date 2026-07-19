"""意图与上下文共用的单轮、安全模型输入合同。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class SemanticContextPacket:
    """每轮至多发送一次，且不包含聊天原文历史或内部 Artifact 标识。"""

    current_message: str
    deterministic_parse: dict[str, Any]
    active_case: dict[str, Any] = field(default_factory=dict)
    recent_turns: tuple[dict[str, Any], ...] = ()
    pending_clarification: dict[str, Any] | None = None
    context_candidates: tuple[dict[str, Any], ...] = ()
    allowed_capabilities: tuple[str, ...] = ()
    allowed_source_kinds: tuple[str, ...] = ("prior_result", "current_message")
    temperature: Literal[0] = 0
    response_schema: Literal["semantic_turn_proposal.v1"] = "semantic_turn_proposal.v1"
    schema_version: Literal["semantic_context_packet.v1"] = "semantic_context_packet.v1"

    # 一轮迁移期兼容只读属性；调用方应改用上面的统一字段。
    @property
    def text(self) -> str:
        return self.current_message

    @property
    def deterministic_entities(self) -> tuple[dict[str, Any], ...]:
        return tuple(self.deterministic_parse.get("entities") or ())

    @property
    def deterministic_clauses(self) -> tuple[dict[str, Any], ...]:
        return tuple(self.deterministic_parse.get("clauses") or ())

    @property
    def pending_summary(self) -> dict[str, Any] | None:
        return self.pending_clarification


# 兼容已有扩展和评测代码；生产构造统一使用 SemanticContextPacket。
SemanticModelRequest = SemanticContextPacket


__all__ = ["SemanticContextPacket", "SemanticModelRequest"]
