"""Typed contracts for the isolated Canonical Turn Phase 1 preview."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


CAPABILITY_ALLOWLIST = frozenset(
    {
        "check_runtime_status",
        "compare_runtime_status",
        "create_workorder_draft",
        "diagnose_fault",
        "explain_fault_code",
        "generate_report",
        "resolution_recommendation",
    }
)

GoalOrigin = Literal["explicit", "inferred", "dependency"]
PendingStatus = Literal["waiting", "resumed", "consumed", "expired", "cancelled"]
BindingKind = Literal["slot_only", "mixed", "new_action", "unrelated", "no_match"]


class CanonicalContract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class EntitySpan(CanonicalContract):
    entity_id: str
    kind: Literal[
        "fault_code",
        "device_reference",
        "time_window",
        "artifact_reference",
        "source_reference",
        "correction_reference",
        "deictic_reference",
    ]
    value: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_bounds(self) -> "EntitySpan":
        if self.end <= self.start:
            raise ValueError("entity span end must be greater than start")
        return self


class ClauseAction(CanonicalContract):
    capability: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    entity_refs: list[str] = Field(default_factory=list)
    inferred: bool = False

    @field_validator("capability")
    @classmethod
    def capability_is_allowlisted(cls, value: str) -> str:
        if value not in CAPABILITY_ALLOWLIST:
            raise ValueError(f"capability is not allowlisted: {value}")
        return value


class ClauseSource(CanonicalContract):
    source_kind: Literal["artifact", "prior_result", "current_message"]
    entity_refs: list[str] = Field(default_factory=list)
    relation: str = "input_to_action"


class StructuredClause(CanonicalContract):
    clause_index: int = Field(ge=0)
    text: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    action: ClauseAction | None = None
    source: ClauseSource | None = None
    slot: dict[str, list[str]] = Field(default_factory=dict)
    linker: str | None = None
    parser_source: Literal["deterministic", "model"] = "deterministic"

    @model_validator(mode="after")
    def validate_bounds(self) -> "StructuredClause":
        if self.end <= self.start:
            raise ValueError("clause span end must be greater than start")
        return self


class CurrentUtteranceParse(CanonicalContract):
    schema_version: Literal["current_utterance_parse.v1"] = "current_utterance_parse.v1"
    raw_text: str
    entities: list[EntitySpan] = Field(default_factory=list)
    clauses: list[StructuredClause] = Field(default_factory=list)
    clarification_needs: list[str] = Field(default_factory=list)
    deterministic_confident: bool = False
    model_used: bool = False
    model_rejection_reason: str | None = None

    @model_validator(mode="after")
    def spans_belong_to_raw_text(self) -> "CurrentUtteranceParse":
        entity_ids: set[str] = set()
        for entity in self.entities:
            if entity.entity_id in entity_ids:
                raise ValueError(f"duplicate entity id: {entity.entity_id}")
            entity_ids.add(entity.entity_id)
            if entity.end > len(self.raw_text) or self.raw_text[entity.start : entity.end] != entity.text:
                raise ValueError(f"invalid entity span: {entity.entity_id}")
        for expected_index, clause in enumerate(self.clauses):
            if clause.clause_index != expected_index:
                raise ValueError("clause indexes must be contiguous and ordered")
            if clause.end > len(self.raw_text) or self.raw_text[clause.start : clause.end] != clause.text:
                raise ValueError(f"invalid clause span: {clause.clause_index}")
            refs = set(clause.action.entity_refs if clause.action else [])
            refs.update(clause.source.entity_refs if clause.source else [])
            for values in clause.slot.values():
                refs.update(values)
            missing = refs - entity_ids
            if missing:
                raise ValueError(f"clause references unknown entities: {sorted(missing)}")
        return self


class GoalProvenance(CanonicalContract):
    parser_source: Literal["deterministic", "model", "pending"]
    utterance_span: tuple[int, int] | None = None
    entity_refs: list[str] = Field(default_factory=list)
    pending_id: str | None = None
    original_goal_id: str | None = None


class CanonicalGoal(CanonicalContract):
    schema_version: Literal["canonical_goal.v1"] = "canonical_goal.v1"
    goal_id: str
    capability: str
    origin: GoalOrigin
    user_requested: bool
    user_visible: bool
    clause_index: int = Field(ge=0)
    required_slots: list[str] = Field(default_factory=list)
    resolved_slots: dict[str, Any] = Field(default_factory=dict)
    missing_slots: list[str] = Field(default_factory=list)
    source_requirements: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    provenance: GoalProvenance

    @field_validator("capability")
    @classmethod
    def capability_is_allowlisted(cls, value: str) -> str:
        if value not in CAPABILITY_ALLOWLIST:
            raise ValueError(f"capability is not allowlisted: {value}")
        return value

    @model_validator(mode="after")
    def validate_goal_semantics(self) -> "CanonicalGoal":
        expected_missing = [slot for slot in self.required_slots if slot not in self.resolved_slots]
        if self.missing_slots != expected_missing:
            raise ValueError("missing_slots must equal unresolved required_slots in required-slot order")
        if self.origin == "dependency" and (self.user_requested or self.user_visible):
            raise ValueError("dependency goals cannot be user-requested or user-visible")
        if self.origin != "dependency" and not self.user_requested:
            raise ValueError("explicit and inferred goals must be user-requested")
        return self


class PendingSourceBinding(CanonicalContract):
    source_kind: Literal["artifact", "prior_result", "candidate_context"]
    reference_text: str
    source_id: str | None = None
    audit_only: bool = True


class PendingClarification(CanonicalContract):
    schema_version: Literal["pending_clarification.v1"] = "pending_clarification.v1"
    pending_id: str
    thread_id: str
    user_id: str
    status: PendingStatus = "waiting"
    version: int = Field(default=1, ge=1)
    idempotency_key: str
    created_turn_id: str
    created_message_id: str
    resumed_turn_id: str | None = None
    resumed_message_id: str | None = None
    consumed_turn_id: str | None = None
    consumed_message_id: str | None = None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    original_goals: list[CanonicalGoal]
    missing_slots: list[str]
    candidate_values: dict[str, list[str]] = Field(default_factory=dict)
    source_bindings: list[PendingSourceBinding] = Field(default_factory=list)
    historical_authorization_audit: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_pending(self) -> "PendingClarification":
        if not self.original_goals:
            raise ValueError("pending clarification requires at least one original goal")
        if not self.missing_slots:
            raise ValueError("pending clarification requires missing slots")
        if self.expires_at <= self.created_at:
            raise ValueError("pending clarification expiry must follow creation")
        return self


class PendingBinding(CanonicalContract):
    schema_version: Literal["pending_binding.v1"] = "pending_binding.v1"
    kind: BindingKind
    pending_id: str | None = None
    resolved_slots: dict[str, Any] = Field(default_factory=dict)
    restored_goal_ids: list[str] = Field(default_factory=list)
    appended_goal_ids: list[str] = Field(default_factory=list)
    consumes_pending: bool = False
    reason: str = ""


class CanonicalTurnRequest(CanonicalContract):
    schema_version: Literal["canonical_turn_request.v1"] = "canonical_turn_request.v1"
    request_id: str
    thread_id: str
    user_id: str
    turn_id: str
    message_id: str
    idempotency_key: str
    raw_message: str
    current_parse: CurrentUtteranceParse
    goals: list[CanonicalGoal] = Field(default_factory=list)
    pending_binding: PendingBinding
    authorization_required: bool = True
    authorization_result: None = None

    @model_validator(mode="after")
    def goals_follow_clause_order(self) -> "CanonicalTurnRequest":
        current_goal_indexes = [
            goal.clause_index for goal in self.goals if goal.provenance.parser_source != "pending"
        ]
        if current_goal_indexes != sorted(current_goal_indexes):
            raise ValueError("current-message goals must follow clause order")
        if len({goal.goal_id for goal in self.goals}) != len(self.goals):
            raise ValueError("goal ids must be unique")
        return self


class PendingTransitionProposal(CanonicalContract):
    action: Literal["none", "create_waiting", "keep_waiting", "resume_and_consume"] = "none"
    pending_id: str | None = None
    expected_status: PendingStatus | None = None
    expected_version: int | None = None
    proposed_pending: PendingClarification | None = None
    transition_idempotency_keys: list[str] = Field(default_factory=list)


class TurnCommand(CanonicalContract):
    schema_version: Literal["turn_command.v1"] = "turn_command.v1"
    command: Literal["preview"] = "preview"
    thread_id: str
    user_id: str
    turn_id: str
    message_id: str
    idempotency_key: str
    raw_message: str
    candidate_values: dict[str, list[str]] = Field(default_factory=dict)
    source_bindings: list[PendingSourceBinding] = Field(default_factory=list)
    historical_authorization_audit: dict[str, Any] = Field(default_factory=dict)


class TurnEvent(CanonicalContract):
    schema_version: Literal["turn_event.v1"] = "turn_event.v1"
    event_type: Literal["utterance_parsed", "pending_loaded", "pending_bound", "request_built"]
    sequence: int = Field(ge=0)
    detail: dict[str, Any] = Field(default_factory=dict)


class TurnResult(CanonicalContract):
    schema_version: Literal["turn_result.v1"] = "turn_result.v1"
    request: CanonicalTurnRequest
    events: list[TurnEvent]
    pending_transition: PendingTransitionProposal
    execution_performed: Literal[False] = False
