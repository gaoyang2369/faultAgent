"""Agent Engine V2 contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from fault_diagnosis.domain.artifacts import ArtifactEnvelope, ArtifactLineage, ArtifactManifest


ENGINE_VERSION = "v2"
PLAN_SNAPSHOT_V2_SCHEMA_VERSION = "agent_engine_plan_snapshot.v2"

NodeStatus = Literal["pending", "running", "completed", "skipped", "blocked", "failed", "cancelled"]
PlanSnapshotStatus = Literal["not_implemented", "planned", "validated", "validated_degraded", "blocked", "failed"]
RiskLevel = Literal["low", "medium", "high", "critical"]
FailurePolicy = Literal[
    "continue_independent_goals",
    "continue_degraded",
    "block_dependents",
    "block_all",
]
ArtifactInputRole = Literal[
    "runtime_sql_source",
    "knowledge_source",
    "comparison_member",
    "analysis_source",
    "report_source",
    "tabular_source",
    "workorder_source",
]


class AgentEngineContract(BaseModel):
    """Base model for V2 contracts."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class IntentFrame(AgentEngineContract):
    """Structured understanding of the current user request."""

    schema_version: str = "intent_frame.v1"
    raw_message: str = ""
    normalized_message: str = ""
    language: str = "unknown"
    intent_candidates: list[dict[str, Any]] = Field(default_factory=list)
    primary_intent: str = ""
    sub_intents: list[str] = Field(default_factory=list)
    entities: dict[str, Any] = Field(default_factory=dict)
    device_refs: list[str] = Field(default_factory=list)
    fault_code_refs: list[str] = Field(default_factory=list)
    time_window: dict[str, Any] = Field(default_factory=dict)
    requested_outputs: list[str] = Field(default_factory=list)
    risk_hints: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    model_trace: dict[str, Any] = Field(default_factory=dict)


class RewriteFrame(AgentEngineContract):
    """User and retrieval query rewrites."""

    schema_version: str = "rewrite_frame.v1"
    user_rewrite: str = ""
    rewrite_reason: str = ""
    retrieval_queries: list[str] = Field(default_factory=list)
    sql_question: str = ""
    manual_query: str = ""
    kg_query: str = ""
    staleness_note: str = ""


class ContextFrame(AgentEngineContract):
    """Resolved multi-turn context for the current request."""

    schema_version: str = "context_frame.v1"
    relation_to_previous: str = "new_case"
    active_case_id: str | None = None
    referenced_artifact_id: str | None = None
    inherited_slots: dict[str, Any] = Field(default_factory=dict)
    stale_evidence: list[str] = Field(default_factory=list)
    missing_context: list[str] = Field(default_factory=list)
    permission_context: dict[str, Any] = Field(default_factory=dict)
    reuse_decision: str = "collect_new"
    reuse_blockers: list[str] = Field(default_factory=list)


class EffectiveGoal(AgentEngineContract):
    """Canonical executable capability plus its user-visible deliverables."""

    schema_version: str = "effective_goal.v1"
    goal_id: str
    capability: str
    target_scope_id: str | None = None
    requested_deliverables: list[str] = Field(default_factory=list)
    required_slots: list[str] = Field(default_factory=list)
    optional_slots: list[str] = Field(default_factory=list)
    evidence_requirements: list[str] = Field(default_factory=list)
    source_policy: str = "collect_new"
    priority: int = 100
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    explicit: bool = True
    depends_on_goal_ids: list[str] = Field(default_factory=list)
    origin: Literal["explicit", "inferred", "dependency"] = "explicit"
    clause_index: int = 0
    user_requested: bool = True
    user_visible: bool = True


class RequestedGoalSet(AgentEngineContract):
    """User-requested goals derived only from the current message and IntentFrame."""

    schema_version: str = "requested_goal_set.v1"
    goals: list[EffectiveGoal] = Field(default_factory=list)
    primary_goal_id: str = ""


class SourceBinding(AgentEngineContract):
    """An exact, verified binding to a committed historical artifact."""

    schema_version: str = "source_binding.v1"
    goal_id: str = ""
    artifact_id: str
    artifact_type: str
    thread_id: str
    lineage_status: Literal["complete", "legacy_partial", "invalid"] = "legacy_partial"
    persistence_status: Literal["staged", "committed", "failed", "not_persisted"] = "not_persisted"
    readback_verified: bool = False
    source_policy: str = ""
    selection_reason: str = ""
    reusable_result_artifact_id: str | None = None
    idempotency_key: str | None = None


class InternalPrerequisite(AgentEngineContract):
    """Runtime work needed to serve requested goals without becoming a user goal."""

    schema_version: str = "internal_prerequisite.v1"
    prerequisite_id: str
    node_type: str
    serves_goal_ids: list[str] = Field(default_factory=list)
    reason: str = ""


class TargetScope(AgentEngineContract):
    schema_version: str = "target_scope.v1"
    scope_id: str = "scope_current"
    operation: Literal["keep", "replace", "compare"] = "keep"
    included_devices: list[str] = Field(default_factory=list)
    excluded_devices: list[str] = Field(default_factory=list)
    resolved_devices: list[str] = Field(default_factory=list)
    explicit_switch: bool = False
    source: Literal["current_message", "artifact", "case_state", "context_signal"] = "current_message"


class GoalQuerySpec(AgentEngineContract):
    schema_version: str = "goal_query_spec.v1"
    query_spec_id: str
    goal_id: str
    capability: str
    sql_question: str | None = None
    rag_query: str | None = None
    analysis_request: dict[str, Any] | None = None
    target_devices: list[str] = Field(default_factory=list)
    fault_codes: list[str] = Field(default_factory=list)


class EffectiveRequestFrame(AgentEngineContract):
    """Context-aware request consumed by routing, planning and runtime prep."""

    schema_version: str = "effective_request.v1"
    raw_message: str = ""
    normalized_message: str = ""
    original_semantic_intent: str = ""
    effective_semantic_intent: str = ""
    authorized_semantic_intent: str = ""
    executed_semantic_intent: str = ""
    original_requested_action: str = ""
    original_task_family: str = ""
    authorization_decision: dict[str, Any] = Field(default_factory=dict)
    semantic_intent: str = ""
    task_family: str = ""
    requested_action: str = ""
    requested_output_mode: str = "concise"
    effective_device_refs: list[str] = Field(default_factory=list)
    effective_fault_code_refs: list[str] = Field(default_factory=list)
    effective_time_window: dict[str, Any] = Field(default_factory=dict)
    target_artifact_id: str | None = None
    target_artifact_type: str | None = None
    target_evidence_bundle_id: str | None = None
    target_report_id: str | None = None
    selected_case_id: str | None = None
    slot_sources: dict[str, str] = Field(default_factory=dict)
    available_actions: list[str] = Field(default_factory=list)
    evidence_policy: str = "collect_new"
    freshness: str = "unknown"
    stale_evidence_disclosure_required: bool = False
    needs_clarification: bool = False
    clarification_question: str = ""
    ambiguity: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    resolution_trace: list[dict[str, Any]] = Field(default_factory=list)
    safety_flags: list[str] = Field(default_factory=list)
    requested_goal_set: RequestedGoalSet = Field(default_factory=RequestedGoalSet)
    target_scope: TargetScope = Field(default_factory=TargetScope)
    goal_query_specs: list[GoalQuerySpec] = Field(default_factory=list)
    requested_goals: list[str] = Field(default_factory=list)
    authorized_goal_ids: list[str] = Field(default_factory=list)
    dropped_goals: list[dict[str, Any]] = Field(default_factory=list)
    clarification_reasons: list[dict[str, Any]] = Field(default_factory=list)
    discarded_artifact_ids: list[str] = Field(default_factory=list)
    source_bindings: list[SourceBinding] = Field(default_factory=list)
    internal_prerequisites: list[InternalPrerequisite] = Field(default_factory=list)

    @property
    def effective_goal_set(self) -> RequestedGoalSet:
        """Read-only compatibility alias; requested_goal_set is authoritative."""

        return self.requested_goal_set


class SkillRoute(AgentEngineContract):
    """Selected skill package set for this request."""

    schema_version: str = "skill_route.v1"
    selected_skills: list[str] = Field(default_factory=list)
    primary_skill: str = ""
    skill_inputs: dict[str, Any] = Field(default_factory=dict)
    load_set: list[str] = Field(default_factory=list)
    skill_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    routing_reason: str = ""
    blocked_skills: dict[str, str] = Field(default_factory=dict)


class _DictCompatContract(AgentEngineContract):
    """Small mapping-style bridge for older runtime/projection code."""

    def get(self, key: str, default: Any = None) -> Any:
        field = self._field_for_key(key)
        return getattr(self, field, default) if field else default

    def __getitem__(self, key: str) -> Any:
        field = self._field_for_key(key)
        if not field:
            raise KeyError(key)
        return getattr(self, field)

    def __setitem__(self, key: str, value: Any) -> None:
        field = self._field_for_key(key)
        if not field:
            raise KeyError(key)
        setattr(self, field, value)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and self._field_for_key(key) is not None

    @classmethod
    def _field_for_key(cls, key: str) -> str | None:
        if key in cls.model_fields:
            return key
        for field_name, field_info in cls.model_fields.items():
            if field_info.alias == key:
                return field_name
        return None


class PlanGoal(_DictCompatContract):
    """Normalized goal in a V2 execution snapshot."""

    goal_id: str = ""
    goal: str = ""
    skill: str = ""
    goal_type: str = ""
    description: str = ""
    device_refs: list[str] = Field(default_factory=list)
    fault_code_refs: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = "low"
    source: str = "skill_compiler"
    capability: str = ""
    target_scope_id: str | None = None
    requested_deliverables: list[str] = Field(default_factory=list)
    required_slots: list[str] = Field(default_factory=list)
    optional_slots: list[str] = Field(default_factory=list)
    evidence_requirements: list[str] = Field(default_factory=list)
    source_policy: str = "collect_new"
    priority: int = 100
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    depends_on_goal_ids: list[str] = Field(default_factory=list)
    authorization_status: Literal["authorized", "denied"] = "authorized"
    drop_reason: str = ""
    origin: Literal["explicit", "inferred", "dependency"] = "explicit"
    clause_index: int = 0
    user_requested: bool = True
    user_visible: bool = True
    readiness_status: str = "ready"
    source_resolution_status: str = "requires_execution"
    source_artifact_id: str = ""
    source_artifact_type: str = ""
    source_freshness: str = "unknown"
    execution_error_code: str = ""
    execution_error_message: str = ""


class PlanEdge(_DictCompatContract):
    """Directed dependency between typed execution nodes."""

    source: str = Field(default="", alias="from")
    target: str = Field(default="", alias="to")
    condition: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ArtifactRoleBinding(AgentEngineContract):
    schema_version: Literal["artifact_role_binding.v1"] = "artifact_role_binding.v1"
    goal_id: str
    node_id: str
    role: ArtifactInputRole
    artifact_id: str
    artifact_type: str
    producer_goal_id: str | None = None
    producer_node_id: str | None = None
    required: bool = True
    device_ref: str | None = None
    member_order: int | None = Field(default=None, ge=0)


class GoalScopedNodeInputs(AgentEngineContract):
    goal_id: str = ""
    node_id: str = ""
    goal_ids: list[str] = Field(default_factory=list)
    query_spec_id: str = ""
    target_scope_id: str = ""
    artifact_role_bindings: list[ArtifactRoleBinding] = Field(default_factory=list)
    preparation_error: str = ""
    artifact_id: str = ""
    canonical_capability: str = ""
    canonical_raw_message: str = ""
    canonical_resolved_slots: dict[str, Any] = Field(default_factory=dict)
    source_freshness: str = "unknown"


class SqlNodeInputs(GoalScopedNodeInputs):
    sql_query: str = ""
    use_checker: bool = False
    equipment_hint: str = ""
    fault_code_hint: str = ""
    degraded_notice: str = ""
    device_refs: list[str] = Field(default_factory=list)
    fault_code_refs: list[str] = Field(default_factory=list)
    requested_tables: list[str] = Field(default_factory=list)
    context_relation: str = ""
    requested_output_mode: str = ""
    requested_action: str = ""
    semantic_intent: str = ""
    target_artifact_id: str | None = None
    target_artifact_type: str | None = None
    target_evidence_bundle_id: str | None = None
    target_report_id: str | None = None
    stale_evidence_disclosure_required: bool = False
    compatibility_only: bool = True


class RagNodeInputs(GoalScopedNodeInputs):
    query: str = ""
    retrieval_strategy: str = ""
    top_k: int = Field(default=1, ge=1, le=10)
    source_artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    device_refs: list[str] = Field(default_factory=list)
    fault_code_refs: list[str] = Field(default_factory=list)
    context_relation: str = ""
    requested_output_mode: str = ""
    requested_action: str = ""
    semantic_intent: str = ""
    target_artifact_id: str | None = None
    target_artifact_type: str | None = None
    target_evidence_bundle_id: str | None = None
    target_report_id: str | None = None
    stale_evidence_disclosure_required: bool = False
    compatibility_only: bool = True


class AnalysisNodeInputs(GoalScopedNodeInputs):
    device_refs: list[str] = Field(default_factory=list)
    fault_code_refs: list[str] = Field(default_factory=list)
    context_relation: str = "canonical_turn"
    requested_output_mode: str = "concise"


class ComparisonNodeInputs(GoalScopedNodeInputs):
    device_refs: list[str] = Field(default_factory=list)
    fault_code_refs: list[str] = Field(default_factory=list)
    context_relation: str = "canonical_turn"
    requested_output_mode: str = "concise"


class ReportNodeInputs(GoalScopedNodeInputs):
    title: str = ""
    chart_payload: Any = None
    operation_report_payload: str = ""
    report_filename: str = ""
    diagnosis_type: str = ""
    device_refs: list[str] = Field(default_factory=list)
    fault_code_refs: list[str] = Field(default_factory=list)
    context_relation: str = ""
    requested_output_mode: str = ""
    requested_action: str = ""
    semantic_intent: str = ""
    target_artifact_id: str | None = None
    target_artifact_type: str | None = None
    target_evidence_bundle_id: str | None = None
    target_report_id: str | None = None
    stale_evidence_disclosure_required: bool = False
    compatibility_only: bool = True


class WorkorderNodeInputs(GoalScopedNodeInputs):
    create_draft: bool = True
    action_type: str = ""
    workorder_action: str = ""
    stale_refresh_required: bool = False
    device_refs: list[str] = Field(default_factory=list)
    fault_code_refs: list[str] = Field(default_factory=list)
    context_relation: str = ""
    requested_output_mode: str = ""
    semantic_intent: str = ""
    target_artifact_id: str | None = None
    target_artifact_type: str | None = None
    target_evidence_bundle_id: str | None = None
    target_report_id: str | None = None
    stale_evidence_disclosure_required: bool = False
    source_artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    selected_findings_summary: list[str] = Field(default_factory=list)
    risk_level: str = ""
    diagnosis_summary: str = ""
    evidence_freshness: str = ""
    report_url: str = ""
    manual_confirmation_required: bool = True
    draft_only: bool = True
    artifact_access_error: str = ""
    reuse_existing_artifact_id: str = ""
    idempotency_key: str = ""
    source_policy: str = ""
    source_selection_reason: str = ""
    compatibility_only: bool = True


class ApprovalNodeInputs(GoalScopedNodeInputs):
    approval_requirements: list[dict[str, Any]] = Field(default_factory=list)
    interrupt_id: str = ""
    device_refs: list[str] = Field(default_factory=list)
    fault_code_refs: list[str] = Field(default_factory=list)
    context_relation: str = ""
    requested_output_mode: str = ""
    requested_action: str = ""
    semantic_intent: str = ""
    target_artifact_id: str | None = None
    target_artifact_type: str | None = None
    target_evidence_bundle_id: str | None = None
    target_report_id: str | None = None
    stale_evidence_disclosure_required: bool = False
    compatibility_only: bool = True


class PlanNode(_DictCompatContract):
    """Typed execution node contract consumed by WorkflowRuntimeExecutor."""

    node_id: str = ""
    node_type: str = ""
    skill: str = ""
    goal_id: str = ""
    status: NodeStatus = "pending"
    inputs: dict[str, Any] = Field(default_factory=dict)
    required_tools: list[str] = Field(default_factory=list)
    retry: dict[str, Any] = Field(default_factory=dict)
    condition: dict[str, Any] = Field(default_factory=dict)
    requested_tables: list[str] = Field(default_factory=list)
    goal_ids: list[str] = Field(default_factory=list)
    query_spec_id: str = ""
    target_scope_id: str = ""
    failure_policy: FailurePolicy = "block_all"
    planned_output_artifact_id: str = ""


class ExecutionPlan(AgentEngineContract):
    """Validated or candidate plan shape consumed by the V2 runtime."""

    schema_version: str = "execution_plan.v1"
    plan_id: str = ""
    plan_version: str = "v2.empty"
    goals: list[PlanGoal] = Field(default_factory=list)
    nodes: list[PlanNode] = Field(default_factory=list)
    edges: list[PlanEdge] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = "low"
    interrupts: list[dict[str, Any]] = Field(default_factory=list)
    approval_requirements: list[dict[str, Any]] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    fallbacks: list[dict[str, Any]] = Field(default_factory=list)
    execution_capability: str = ""
    output_contract: dict[str, Any] = Field(default_factory=dict)


class NodeResult(AgentEngineContract):
    """Result of a typed workflow node."""

    schema_version: str = "node_result.v1"
    node_id: str = ""
    node_type: str = ""
    status: NodeStatus = "pending"
    input_summary: str = ""
    output: Any = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    tool_call_refs: list[str] = Field(default_factory=list)
    error: dict[str, Any] | None = None
    retry_count: int = Field(default=0, ge=0)
    duration_ms: float = Field(default=0.0, ge=0.0)
    goal_ids: list[str] = Field(default_factory=list)
    query_spec_id: str = ""
    target_scope_id: str = ""
    artifact_id: str = ""


class EvidenceLedger(AgentEngineContract):
    """V2 evidence ledger, later projected to the legacy EvidenceBundle."""

    schema_version: str = "evidence_ledger.v1"
    ledger_id: str = ""
    task: dict[str, Any] = Field(default_factory=dict)
    evidence_items: list[dict[str, Any]] = Field(default_factory=list)
    claims: list[dict[str, Any]] = Field(default_factory=list)
    final_claim_ids: list[str] = Field(default_factory=list)
    quality_checks: dict[str, Any] = Field(default_factory=dict)
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    authorization_refs: list[dict[str, Any]] = Field(default_factory=list)


class GoalExecutionResult(AgentEngineContract):
    """Goal-scoped terminal execution projection consumed by Output and trace."""

    schema_version: str = "goal_execution_result.v1"
    goal_id: str
    capability: str
    user_requested: bool
    status: Literal["pending", "completed", "incomplete", "failed", "blocked", "denied", "skipped"]
    planned_node_ids: list[str] = Field(default_factory=list)
    executed_node_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    satisfied_by_artifact: bool = False
    blocked_by_goal_ids: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None


class DeliverableResult(AgentEngineContract):
    """Exactly one user-visible terminal result for one canonical Goal."""

    schema_version: str = "deliverable_result.v2"
    deliverable_id: str = ""
    goal_id: str = ""
    capability: str = ""
    clause_index: int = 0
    user_requested: bool = True
    status: Literal["completed", "partial", "failed", "blocked", "denied", "skipped"]
    title: str = ""
    summary: str | None = None
    structured_content: dict[str, Any] = Field(default_factory=dict)
    artifact_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    dependency_goal_ids: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    blocking_goal_ids: list[str] = Field(default_factory=list)
    source_freshness: str | None = None
    source_generated_at: str | None = None

    @model_validator(mode="after")
    def fill_deliverable_id(self) -> "DeliverableResult":
        if not self.deliverable_id and self.goal_id:
            self.deliverable_id = f"deliverable:{self.goal_id}"
        return self


class LegacyDeliverableProjection(AgentEngineContract):
    """Deprecated one-way aliases emitted only at public serialization boundaries."""

    deliverable_type: Literal[
        "fault_code_explanation",
        "runtime_status",
        "runtime_comparison",
        "diagnosis",
        "recommendations",
        "report",
        "workorder_draft",
        "clarification",
        "permission_denied",
    ] = "clarification"
    payload: dict[str, Any] = Field(default_factory=dict)
    source_artifact_ids: list[str] = Field(default_factory=list)
    compatibility_only: Literal[True] = True


class CompositeOutputFrame(AgentEngineContract):
    schema_version: str = "composite_output_frame.v1"
    deliverables: list[DeliverableResult] = Field(default_factory=list)
    overall_status: str = "not_implemented"
    content: str = ""
    answer_variant: str = "not_implemented"
    legacy_answer_variant: str = "not_implemented"

    @model_validator(mode="after")
    def validate_one_deliverable_per_goal(self) -> "CompositeOutputFrame":
        goal_ids = [item.goal_id for item in self.deliverables if item.goal_id]
        if len(goal_ids) != len(set(goal_ids)):
            raise ValueError("each requested goal must produce exactly one deliverable")
        return self


class OutputFrame(AgentEngineContract):
    """V2 output before compatibility projection."""

    schema_version: str = "output_frame.v1"
    answer_variant: str = "not_implemented"
    final_answer: str = ""
    status_brief: str = ""
    diagnosis_report_payload: dict[str, Any] = Field(default_factory=dict)
    workorder_draft_payload: dict[str, Any] = Field(default_factory=dict)
    sse_payload: dict[str, Any] = Field(default_factory=dict)
    artifact_payload: dict[str, Any] = Field(default_factory=dict)
    guardrail_result: dict[str, Any] = Field(default_factory=dict)
    runtime_status_assessment: dict[str, Any] = Field(default_factory=dict)
    contract_validation: dict[str, Any] = Field(default_factory=dict)
    composite_output: CompositeOutputFrame = Field(default_factory=CompositeOutputFrame)
    goal_execution_results: list[GoalExecutionResult] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_status_brief_v2_contract(self) -> "OutputFrame":
        if self.answer_variant != "status_brief_v2":
            return self
        if not self.runtime_status_assessment:
            raise ValueError("status_brief_v2 requires runtime_status_assessment")
        if self.contract_validation.get("contract_satisfied") is not True:
            raise ValueError("status_brief_v2 output contract is not satisfied")
        return self


class PlanSnapshotV2(AgentEngineContract):
    """Side-effect-free V2 plan snapshot for internal tests and future API projection."""

    schema_version: str = PLAN_SNAPSHOT_V2_SCHEMA_VERSION
    engine_version: str = ENGINE_VERSION
    status: PlanSnapshotStatus = "not_implemented"
    intent_frame: IntentFrame = Field(default_factory=IntentFrame)
    rewrite_frame: RewriteFrame = Field(default_factory=RewriteFrame)
    context_frame: ContextFrame = Field(default_factory=ContextFrame)
    effective_request_frame: EffectiveRequestFrame = Field(default_factory=EffectiveRequestFrame)
    skill_route: SkillRoute = Field(default_factory=SkillRoute)
    execution_plan: ExecutionPlan = Field(default_factory=ExecutionPlan)
    node_results: list[NodeResult] = Field(default_factory=list)
    evidence_ledger: EvidenceLedger = Field(default_factory=EvidenceLedger)
    output_frame: OutputFrame = Field(default_factory=OutputFrame)
    trace: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
