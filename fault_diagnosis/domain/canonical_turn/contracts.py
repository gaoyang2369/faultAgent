"""Authoritative contracts for one canonical conversation turn."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .capabilities import CANONICAL_CAPABILITIES

SHADOW_ONLY_CAPABILITIES = frozenset(
    {"meta", "unsupported_high_risk_action"}
)
ALL_INTENT_CAPABILITIES = CANONICAL_CAPABILITIES | SHADOW_ONLY_CAPABILITIES
# Backward-compatible production name. It intentionally excludes Shadow-only
# candidates and remains the authority for ClauseAction and CanonicalGoal.
CAPABILITY_ALLOWLIST = CANONICAL_CAPABILITIES

GoalOrigin = Literal["explicit", "inferred", "dependency"]
PendingStatus = Literal["waiting", "resumed", "consumed", "expired", "cancelled"]
BindingKind = Literal["slot_only", "mixed", "new_action", "unrelated", "no_match"]
AuthorizationStatus = Literal["authorized", "denied"]
ReadinessStatus = Literal[
    "ready",
    "satisfied_by_artifact",
    "blocked_missing_slot",
    "blocked_source",
    "blocked_permission",
]
SourceResolutionStatus = Literal[
    "satisfied_by_artifact",
    "source_for_execution",
    "requires_execution",
    "ambiguous",
    "unresolved",
    "incompatible",
    "stale",
    "blocked",
]
GoalTerminalStatus = Literal["pending", "completed", "failed", "blocked", "denied", "satisfied", "skipped"]
BindingStatus = Literal["bound", "partially_bound", "needs_clarification", "blocked", "refresh_required"]


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
        if value not in CANONICAL_CAPABILITIES:
            raise ValueError(f"capability is not allowlisted: {value}")
        return value


class ClauseSource(CanonicalContract):
    source_kind: Literal["artifact", "prior_result", "current_message"]
    entity_refs: list[str] = Field(default_factory=list)
    relation: str = "input_to_action"


class ClauseModality(CanonicalContract):
    """Production language semantics attached to one deterministic clause."""

    requested: bool = True
    negated: bool = False
    conditional: bool = False
    condition_type: Literal[
        "if_abnormal", "if_fault_confirmed", "if_high_risk", "if_workorder_recommended"
    ] | None = None
    sequence_index: int | None = Field(default=None, ge=0)
    depends_on_clause_indexes: list[int] = Field(default_factory=list)
    relation_to_previous_clause: Literal["sequence", "condition", "contrast", "parallel"] | None = None


class StructuredClause(CanonicalContract):
    clause_index: int = Field(ge=0)
    text: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    action: ClauseAction | None = None
    source: ClauseSource | None = None
    slot: dict[str, list[str]] = Field(default_factory=dict)
    linker: str | None = None
    modality: ClauseModality = Field(default_factory=ClauseModality)
    parser_source: Literal["deterministic", "model"] = "deterministic"

    @model_validator(mode="after")
    def validate_bounds(self) -> "StructuredClause":
        if self.end <= self.start:
            raise ValueError("clause span end must be greater than start")
        return self


class IntentFallbackDecision(CanonicalContract):
    eligible: bool = False
    reason_codes: list[str] = Field(default_factory=list)


class IntentResolutionMetadata(CanonicalContract):
    mode: Literal["deterministic", "llm_fallback", "llm_primary", "deterministic_after_llm_failure"] = "deterministic"
    fallback_attempted: bool = False
    fallback_reasons: list[str] = Field(default_factory=list)
    model_status: str = "not_attempted"
    accepted_model_fields: list[str] = Field(default_factory=list)
    rejected_model_fields: list[str] = Field(default_factory=list)
    duration_ms: float = 0
    model_name: str = ""
    deterministic_capabilities: list[str] = Field(default_factory=list)
    model_capabilities: list[str] = Field(default_factory=list)


class CurrentUtteranceParse(CanonicalContract):
    schema_version: Literal["current_utterance_parse.v1", "current_utterance_parse.v2"] = "current_utterance_parse.v2"
    raw_text: str
    entities: list[EntitySpan] = Field(default_factory=list)
    clauses: list[StructuredClause] = Field(default_factory=list)
    clarification_needs: list[str] = Field(default_factory=list)
    deterministic_confident: bool = False
    model_used: bool = False
    model_rejection_reason: str | None = None
    intent_resolution: IntentResolutionMetadata = Field(default_factory=IntentResolutionMetadata)

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


class GoalExecutionCondition(CanonicalContract):
    predicate: Literal[
        "diagnosis_is_abnormal", "fault_is_confirmed", "risk_is_high", "workorder_is_recommended"
    ]
    source_goal_id: str = ""
    on_false: Literal["skip"] = "skip"
    on_unknown: Literal["block"] = "block"


class CanonicalGoal(CanonicalContract):
    schema_version: Literal["canonical_goal.v1", "canonical_goal.v2"] = "canonical_goal.v2"
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
    execution_condition: GoalExecutionCondition | None = None
    provenance: GoalProvenance

    @field_validator("capability")
    @classmethod
    def capability_is_allowlisted(cls, value: str) -> str:
        if value not in CANONICAL_CAPABILITIES:
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


class ClarificationOption(CanonicalContract):
    option_id: str
    label: str
    asset_ref: str | None = None


class ClarificationRequirement(CanonicalContract):
    reason_code: Literal[
        "ambiguous_asset_reference",
        "ambiguous_source_artifact",
        "missing_asset",
        "missing_source",
    ]
    question: str
    options: list[ClarificationOption] = Field(default_factory=list)


class ContextCandidate(CanonicalContract):
    """ACL-filtered, safe projection of context metadata; never artifact content."""

    candidate_id: str
    artifact_ref: str | None = None
    artifact_type: str | None = None
    asset_refs: list[str] = Field(default_factory=list)
    fault_codes: list[str] = Field(default_factory=list)
    time_window: dict[str, Any] | None = None
    produced_turn_id: str | None = None
    produced_by_immediately_previous_turn: bool = False
    completed: bool = False
    reportable: bool = False
    actionable: bool = False
    freshness_state: str = "unknown"
    lineage_refs: list[str] = Field(default_factory=list)
    report_input_snapshot_schema_version: str = ""
    tabular_source_artifact_ref: str | None = None
    pending_action_type: str | None = None
    source_kind: Literal["artifact_manifest", "case_state", "pending_action", "explicit_reference"]


class ContextSlotBinding(CanonicalContract):
    slot_name: Literal[
        "asset_refs", "fault_codes", "time_window", "source_artifact_refs", "output_target_refs"
    ]
    status: Literal["explicit", "inherited", "unbound", "ambiguous", "unavailable", "refresh_required"]
    values: list[Any] = Field(default_factory=list)
    provenance: Literal[
        "current_message",
        "explicit_correction",
        "immediately_previous_turn",
        "referenced_artifact",
        "pending_action",
        "case_state",
        "recent_thread_artifact",
    ] | None = None
    candidate_ids: list[str] = Field(default_factory=list)
    reason_code: str = ""


class GoalContextBinding(CanonicalContract):
    goal_id: str
    capability: str
    asset_refs: list[str] = Field(default_factory=list)
    fault_codes: list[str] = Field(default_factory=list)
    time_window: dict[str, Any] | None = None
    source_artifact_refs: list[str] = Field(default_factory=list)
    output_target_refs: list[str] = Field(default_factory=list)
    slots: list[ContextSlotBinding] = Field(default_factory=list)
    binding_status: BindingStatus
    blockers: list[str] = Field(default_factory=list)
    clarification: ClarificationRequirement | None = None


class BoundCanonicalTurn(CanonicalContract):
    schema_version: Literal["bound_canonical_turn.v1"] = "bound_canonical_turn.v1"
    canonical_turn: CanonicalTurnRequest
    goal_bindings: list[GoalContextBinding] = Field(default_factory=list)


class GoalAuthorizationDecision(CanonicalContract):
    """Current-turn authorization for exactly one canonical Goal."""

    schema_version: Literal["goal_authorization_decision.v1"] = "goal_authorization_decision.v1"
    goal_id: str
    capability: str
    status: AuthorizationStatus
    reason_code: str = ""
    reason: str = ""
    audit: dict[str, Any] = Field(default_factory=dict)


class GoalSourceResolution(CanonicalContract):
    """Goal-scoped distinction between selecting a source and satisfying a Goal."""

    schema_version: Literal["goal_source_resolution.v1"] = "goal_source_resolution.v1"
    goal_id: str
    status: SourceResolutionStatus
    artifact_id: str | None = None
    artifact_type: str | None = None
    source_freshness: str = "unknown"
    reason: str = ""
    reason_code: str = ""
    candidate_artifact_ids: list[str] = Field(default_factory=list)
    resolved_slots: dict[str, Any] = Field(default_factory=dict)
    reusable_result_artifact_id: str | None = None
    idempotency_key: str | None = None
    tabular_source_artifact_id: str | None = None


class GoalReadinessDecision(CanonicalContract):
    """Execution readiness for exactly one canonical Goal."""

    schema_version: Literal["goal_readiness_decision.v1"] = "goal_readiness_decision.v1"
    goal_id: str
    status: ReadinessStatus
    blockers: list[str] = Field(default_factory=list)


class GoalExecutionStatus(CanonicalContract):
    """Terminal or in-flight execution state for exactly one canonical Goal."""

    schema_version: Literal["goal_execution_status.v1"] = "goal_execution_status.v1"
    goal_id: str
    status: GoalTerminalStatus = "pending"
    node_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    error_code: str | None = None


class PendingTransitionProposal(CanonicalContract):
    action: Literal["none", "create_waiting", "keep_waiting", "resume_and_consume"] = "none"
    pending_id: str | None = None
    expected_status: PendingStatus | None = None
    expected_version: int | None = None
    proposed_pending: PendingClarification | None = None
    transition_idempotency_keys: list[str] = Field(default_factory=list)


class TurnCommand(CanonicalContract):
    schema_version: Literal["turn_command.v1"] = "turn_command.v1"
    command: Literal["execute", "preview"] = "execute"
    thread_id: str
    user_id: str
    turn_id: str
    message_id: str
    idempotency_key: str
    raw_message: str
    candidate_values: dict[str, list[str]] = Field(default_factory=dict)
    source_bindings: list[PendingSourceBinding] = Field(default_factory=list)
    historical_authorization_audit: dict[str, Any] = Field(default_factory=dict)
    channel: Literal["text", "text_edit", "voice", "collect", "plan"] = "text"
    metadata: dict[str, Any] = Field(default_factory=dict)


class TurnEvent(CanonicalContract):
    schema_version: Literal["turn_event.v1"] = "turn_event.v1"
    event_type: str
    sequence: int = Field(ge=0)
    detail: dict[str, Any] = Field(default_factory=dict)


class TurnResult(CanonicalContract):
    schema_version: Literal["turn_result.v1"] = "turn_result.v1"
    request: CanonicalTurnRequest
    bound_turn: BoundCanonicalTurn | None = None
    events: list[TurnEvent]
    pending_transition: PendingTransitionProposal
    authorization: list[GoalAuthorizationDecision] = Field(default_factory=list)
    readiness: list[GoalReadinessDecision] = Field(default_factory=list)
    source_resolutions: list[GoalSourceResolution] = Field(default_factory=list)
    goal_statuses: list[GoalExecutionStatus] = Field(default_factory=list)
    execution_performed: bool = False
    terminal_status: str = "preview"
    plan_snapshot: Any | None = None
    complete_payload: dict[str, Any] = Field(default_factory=dict)
