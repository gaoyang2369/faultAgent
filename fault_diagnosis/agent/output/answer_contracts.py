"""Contracts for the evidence-grounded final-answer presentation layer."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class AnswerSourcePacket(_StrictContract):
    """Authorization-safe facts that the answer model is allowed to express."""

    schema_version: Literal["answer_source_packet.v1"] = "answer_source_packet.v1"
    user_request: str
    overall_status: Literal["completed", "partial", "blocked", "denied", "failed"]
    deliverables: list[dict[str, Any]] = Field(default_factory=list)
    claims: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    data_basis: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    allowed_urls: list[str] = Field(default_factory=list)
    allowed_device_refs: list[str] = Field(default_factory=list)
    allowed_fault_codes: list[str] = Field(default_factory=list)
    allowed_action_states: list[str] = Field(default_factory=list)
    deterministic_fallback: str


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
    answer: str = ""
    used_claim_ids: list[str] = Field(default_factory=list)
    used_evidence_ids: list[str] = Field(default_factory=list)
    limitations_disclosed: bool = False
    data_basis_disclosed: bool = False
    model_name: str = ""
    duration_ms: float = 0.0
    fallback_reason: str = ""
    validation_errors: list[str] = Field(default_factory=list)
    enabled: bool = False
    attempted: bool = False
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
            "model_name": self.model_name,
            "duration_ms": round(self.duration_ms, 1),
            "input_char_count": self.input_char_count,
            "output_char_count": self.output_char_count,
            "input_token_count": self.input_token_count,
            "output_token_count": self.output_token_count,
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
            "model_name": self.model_name,
            "duration_ms": round(self.duration_ms, 1),
            "input_char_count": self.input_char_count,
            "output_char_count": self.output_char_count,
            "input_token_count": self.input_token_count,
            "output_token_count": self.output_token_count,
            "source_packet_compacted": self.source_packet_compacted,
            "used_claim_count": len(self.used_claim_ids),
            "used_evidence_count": len(self.used_evidence_ids),
            "validation_errors": list(self.validation_errors),
            "fallback_reason": self.fallback_reason,
        }
