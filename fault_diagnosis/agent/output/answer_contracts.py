"""Contracts for the evidence-grounded final-answer presentation layer."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


SynthesisStatus = Literal[
    "disabled",
    "generated",
    "model_not_configured",
    "model_timeout",
    "model_error",
    "schema_invalid",
    "validation_failed",
    "source_packet_invalid",
]


class _StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class AnswerFactResult(_StrictContract):
    """One compact, user-facing result; no raw runtime object or internal identifier."""

    capability: str
    status: Literal["completed", "partial", "failed", "blocked", "denied", "skipped"]
    subject: str = ""
    facts: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)
    data_basis: dict[str, Any] = Field(default_factory=dict)
    claim_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    failure_reason: str = ""


class AnswerFacts(_StrictContract):
    """Minimal model-visible facts plus server-only validation metadata."""

    schema_version: Literal["answer_facts.v1"] = "answer_facts.v1"
    user_question: str
    overall_status: Literal["completed", "partial", "blocked", "denied", "failed"]
    results: list[AnswerFactResult] = Field(default_factory=list)

    # Server-only: excluded from the JSON sent to the model.
    allowed_urls: list[str] = Field(default_factory=list, exclude=True)
    allowed_device_refs: list[str] = Field(default_factory=list, exclude=True)
    allowed_fault_codes: list[str] = Field(default_factory=list, exclude=True)
    allowed_action_states: list[str] = Field(default_factory=list, exclude=True)
    claim_ref_map: dict[str, str] = Field(default_factory=dict, exclude=True)
    evidence_ref_map: dict[str, str] = Field(default_factory=dict, exclude=True)
    claim_support_refs: dict[str, list[str]] = Field(default_factory=dict, exclude=True)
    internal_identifiers: list[str] = Field(default_factory=list, exclude=True)
    limitations_required: bool = Field(default=False, exclude=True)
    data_basis_required: bool = Field(default=False, exclude=True)


# One-release compatibility name for imports. Its shape is now AnswerFacts.
AnswerSourcePacket = AnswerFacts


class GroundedAnswerModelOutput(_StrictContract):
    """Exact JSON shape accepted from the answer model."""

    schema_version: Literal["grounded_answer.v1"] = "grounded_answer.v1"
    answer: str
    used_claim_ids: list[str] = Field(default_factory=list)
    used_evidence_ids: list[str] = Field(default_factory=list)
    limitations_disclosed: bool = False
    data_basis_disclosed: bool = False

    @field_validator("answer")
    @classmethod
    def answer_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("answer must not be blank")
        return value.strip()

    @field_validator("used_claim_ids", "used_evidence_ids")
    @classmethod
    def ids_must_be_non_blank_and_unique(cls, value: list[str]) -> list[str]:
        normalized = [str(item).strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("referenced IDs must not be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("referenced IDs must be unique")
        return normalized


class AnswerValidationResult(_StrictContract):
    valid: bool
    schema_valid: bool = False
    errors: list[str] = Field(default_factory=list)
    output: GroundedAnswerModelOutput | None = None


class GroundedAnswerResult(_StrictContract):
    schema_version: Literal["grounded_answer.v1"] = "grounded_answer.v1"
    status: Literal[
        "generated",
        "fallback",
        "disabled",
        "model_error",
        "validation_failed",
    ]
    status_deprecated: Literal[True] = True
    synthesis_status: SynthesisStatus = "disabled"
    final_answer_source: Literal["grounded_model", "deterministic_fallback"] = "deterministic_fallback"
    fallback_used: bool = True
    answer: str = ""
    used_claim_ids: list[str] = Field(default_factory=list)
    used_evidence_ids: list[str] = Field(default_factory=list)
    limitations_disclosed: bool = False
    data_basis_disclosed: bool = False
    model_name: str = ""
    answer_model_name: str = ""
    answer_model_source: Literal["answer_model", "default_model", "injected", "unconfigured"] = "unconfigured"
    duration_ms: float = 0.0
    fallback_reason: str = ""
    validation_errors: list[str] = Field(default_factory=list)
    enabled: bool = False
    attempted: bool = False
    provider_returned: bool = False
    schema_valid: bool = False
    answer_validated: bool = False
    provider_trace_id: str = ""
    request_timeout_seconds: float = 0.0
    prompt_token_count: int = 0
    completion_token_count: int = 0
    reasoning_token_count: int = 0
    time_to_first_token_ms: float = 0.0
    model_total_latency_ms: float = 0.0
    input_char_count: int = 0
    output_char_count: int = 0
    input_token_count: int = 0
    output_token_count: int = 0
    source_packet_compacted: bool = False

    def audit_summary(self) -> dict[str, Any]:
        """Return the safe subset allowed in traces and complete payloads."""

        return {
            "enabled": self.enabled,
            "attempted": self.attempted,
            "status": self.status,
            "status_deprecated": True,
            "synthesis_status": self.synthesis_status,
            "final_answer_source": self.final_answer_source,
            "fallback_used": self.fallback_used,
            "model_name": self.model_name,
            "answer_model_name": self.answer_model_name or self.model_name,
            "answer_model_source": self.answer_model_source,
            "duration_ms": round(self.duration_ms, 1),
            "input_char_count": self.input_char_count,
            "output_char_count": self.output_char_count,
            "input_token_count": self.input_token_count,
            "output_token_count": self.output_token_count,
            "prompt_token_count": self.prompt_token_count or self.input_token_count,
            "completion_token_count": self.completion_token_count or self.output_token_count,
            "reasoning_token_count": self.reasoning_token_count,
            "provider_returned": self.provider_returned,
            "provider_trace_id": self.provider_trace_id,
            "schema_valid": self.schema_valid,
            "answer_validated": self.answer_validated,
            "request_timeout_seconds": self.request_timeout_seconds,
            "time_to_first_token_ms": self.time_to_first_token_ms,
            "model_total_latency_ms": self.model_total_latency_ms,
            "source_packet_compacted": self.source_packet_compacted,
            "used_claim_count": len(self.used_claim_ids),
            "used_evidence_count": len(self.used_evidence_ids),
            "validation_errors": list(self.validation_errors),
            "fallback_reason": self.fallback_reason,
        }

    def complete_summary(self) -> dict[str, Any]:
        """Return safe call metadata without prompt, packet, IDs, or answer content."""

        return {
            "enabled": self.enabled,
            "attempted": self.attempted,
            "status": self.status,
            "status_deprecated": True,
            "synthesis_status": self.synthesis_status,
            "final_answer_source": self.final_answer_source,
            "fallback_used": self.fallback_used,
            "model_name": self.model_name,
            "answer_model_name": self.answer_model_name or self.model_name,
            "answer_model_source": self.answer_model_source,
            "duration_ms": round(self.duration_ms, 1),
            "input_char_count": self.input_char_count,
            "output_char_count": self.output_char_count,
            "input_token_count": self.input_token_count,
            "output_token_count": self.output_token_count,
            "prompt_token_count": self.prompt_token_count or self.input_token_count,
            "completion_token_count": self.completion_token_count or self.output_token_count,
            "reasoning_token_count": self.reasoning_token_count,
            "provider_returned": self.provider_returned,
            "schema_valid": self.schema_valid,
            "answer_validated": self.answer_validated,
            "request_timeout_seconds": self.request_timeout_seconds,
            "time_to_first_token_ms": self.time_to_first_token_ms,
            "model_total_latency_ms": self.model_total_latency_ms,
            "source_packet_compacted": self.source_packet_compacted,
            "used_claim_count": len(self.used_claim_ids),
            "used_evidence_count": len(self.used_evidence_ids),
            "validation_errors": list(self.validation_errors),
            "fallback_reason": self.fallback_reason,
        }
