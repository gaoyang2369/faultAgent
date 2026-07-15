"""Wave 1 的语义调用合同；不保存 prompt 或原始模型输出。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from fault_diagnosis.domain.canonical_turn import CurrentUtteranceParse


class SemanticEntityProposal(BaseModel):
    """模型提出的当前消息实体；必须由 Canonicalizer 二次确认。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["device_reference", "fault_code", "time_window"]
    text: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    normalized_candidate: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class SemanticClauseProposal(BaseModel):
    """模型提出的一个意图分句，不携带权限、工具或 Artifact ID。"""

    model_config = ConfigDict(extra="forbid")

    clause_index: int = Field(ge=0)
    text: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    capability: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    entity_indexes: list[int] = Field(default_factory=list)
    requested: bool = True
    negated: bool = False
    conditional: bool = False
    condition_type: Literal["if_abnormal", "if_fault_confirmed", "if_high_risk", "if_workorder_recommended"] | None = None
    sequence_index: int | None = Field(default=None, ge=0)
    depends_on_clause_indexes: list[int] = Field(default_factory=list)
    source_kind: Literal["prior_result", "current_message"] = "current_message"


class ContextSemanticProposal(BaseModel):
    """模型提出的上下文筛选约束，不含候选或 Artifact 标识。"""

    model_config = ConfigDict(extra="forbid")

    reference_target: Literal[
        "none", "prior_diagnosis_result", "prior_runtime_result", "prior_report", "prior_comparison",
    ] = "none"
    temporal_relation: Literal["previous", "latest", "earliest", "ordinal"] | None = None
    ordinal: int | None = Field(default=None, ge=1)
    include_asset_refs: list[str] = Field(default_factory=list)
    exclude_asset_refs: list[str] = Field(default_factory=list)
    requested_reuse: bool = False
    freshness_intent: Literal["current_required", "historical_ok", "unspecified"] = "unspecified"
    relation: Literal[
        "none", "worse_device_from_previous_comparison", "comparison_member", "related_prior_result",
    ] = "none"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class SemanticTurnProposal(BaseModel):
    """单轮非权威语义提议，禁止透传原始模型对象。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["semantic_turn_proposal.v1"]
    entities: list[SemanticEntityProposal] = Field(default_factory=list)
    clauses: list[SemanticClauseProposal] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    context: ContextSemanticProposal | None = None


class SemanticFieldDecision(BaseModel):
    """Canonicalizer 对模型字段作出的可审计裁决。"""

    model_config = ConfigDict(extra="forbid")

    field: str
    decision: Literal["ACCEPT", "REJECT", "CLARIFY"]
    value: Any = None
    reason_code: str


class SemanticCallTrace(BaseModel):
    """单轮语义调用的可观测投影，供 trace 与调试 payload 使用。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    attempted: bool = False
    mode: Literal["off", "shadow", "primary"] = "off"
    schema_name: str = Field(default="semantic_turn_proposal.v1", alias="schema", serialization_alias="schema")
    status: Literal[
        "not_attempted", "completed", "model_not_configured", "model_timeout",
        "model_cancelled", "model_error", "schema_invalid", "validation_failed",
    ] = "not_attempted"
    accepted: list[str] = Field(default_factory=list)
    rejected: list[str] = Field(default_factory=list)
    clarify: list[str] = Field(default_factory=list)
    fallback: bool = False
    latency_ms: float = 0.0
    input_tokens: int | None = None
    output_tokens: int | None = None
    model_name: str = ""
    concurrency_limited: bool = False
    deterministic_capabilities: list[str] = Field(default_factory=list)
    proposal_capabilities: list[str] = Field(default_factory=list)


class SemanticResolution(BaseModel):
    """一次语义解析的唯一结果，Canonical 层只消费其最终 parse 投影。"""

    model_config = ConfigDict(extra="forbid")

    parsed: CurrentUtteranceParse
    trace: SemanticCallTrace
    field_decisions: list[SemanticFieldDecision] = Field(default_factory=list)
    context_proposal: ContextSemanticProposal | None = None
    context_clarification_reason: str | None = None
