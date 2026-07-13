"""Agent Engine V2 contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ENGINE_VERSION = "v2"
PLAN_SNAPSHOT_V2_SCHEMA_VERSION = "agent_engine_plan_snapshot.v2"

NodeStatus = Literal["pending", "running", "completed", "skipped", "blocked", "failed", "cancelled"]
PlanSnapshotStatus = Literal["not_implemented", "planned", "validated", "validated_degraded", "blocked", "failed"]
RiskLevel = Literal["low", "medium", "high", "critical"]
ArtifactType = Literal[
    "knowledge_artifact",
    "sql_artifact",
    "analysis_artifact",
    "structured_analysis_artifact",
    "report_artifact",
    "workorder_artifact",
    "comparison_artifact",
]

FailurePolicy = Literal[
    "continue_independent_goals",
    "continue_degraded",
    "block_dependents",
    "block_all",
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


class EffectiveGoalSet(AgentEngineContract):
    schema_version: str = "effective_goal_set.v1"
    goals: list[EffectiveGoal] = Field(default_factory=list)
    primary_goal_id: str = ""


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


class ArtifactLineage(AgentEngineContract):
    schema_version: str = "artifact_lineage.v1"
    lineage_status: Literal["complete", "legacy_partial", "invalid"] = "legacy_partial"
    artifact_id: str = ""
    artifact_type: str = ""
    subject_device_refs: list[str] = Field(default_factory=list)
    fault_code_refs: list[str] = Field(default_factory=list)
    source_artifact_ids: list[str] = Field(default_factory=list)
    source_evidence_bundle_ids: list[str] = Field(default_factory=list)
    data_basis: list[dict[str, Any]] = Field(default_factory=list)
    source_tables: list[str] = Field(default_factory=list)
    time_windows: list[dict[str, Any]] = Field(default_factory=list)
    created_from_goal_ids: list[str] = Field(default_factory=list)


class ArtifactManifest(AgentEngineContract):
    """Stable follow-up contract for runtime artifacts.

    The persisted DiagnosisArtifactEnvelope remains the compatibility container;
    manifests are the per-artifact facts used by context resolution.
    """

    schema_version: str = "artifact_manifest.v1"
    artifact_id: str = ""
    artifact_type: ArtifactType
    thread_id: str = ""
    turn_id: str = ""
    request_id: str = ""
    trace_id: str = ""
    produced_by_skill: str = ""
    produced_by_nodes: list[str] = Field(default_factory=list)
    status: Literal["completed", "failed", "partial"] = "completed"
    followupable: bool = False
    reportable: bool = False
    actionable: bool = False
    device_refs: list[str] = Field(default_factory=list)
    fault_code_refs: list[str] = Field(default_factory=list)
    time_window: dict[str, Any] = Field(default_factory=dict)
    source_table: str = ""
    data_window: dict[str, Any] = Field(default_factory=dict)
    requested_window: dict[str, Any] = Field(default_factory=dict)
    resolved_window: dict[str, Any] = Field(default_factory=dict)
    data_basis: dict[str, Any] = Field(default_factory=dict)
    latest_sample_time: str = ""
    sample_count: int = 0
    runtime_status: str = "unknown"
    key_findings: list[str] = Field(default_factory=list)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    freshness: str = "unknown"
    currentness: str = ""
    severity: str = ""
    risk_level: str = ""
    status_level: str = ""
    diagnosis_summary: str = ""
    findings: list[str] = Field(default_factory=list)
    probable_causes: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    evidence_bundle_id: str = ""
    report_url: str = ""
    report_filename: str = ""
    linked_analysis_artifact_id: str = ""
    linked_sql_artifact_id: str = ""
    linked_evidence_bundle_id: str = ""
    source_file: str = ""
    source_page: str = ""
    parsed_manual_fields: dict[str, Any] = Field(default_factory=dict)
    available_followups: list[str] = Field(default_factory=list)
    supported_followup_capabilities: list[str] = Field(default_factory=list)
    available_actions: list[str] = Field(default_factory=list)
    authorization_scope_summary: dict[str, Any] = Field(default_factory=dict)
    draft_only: bool = False
    manual_confirmation_required: bool = False
    dispatch_forbidden: bool = False
    owner_user_id: str = ""
    owner_session_id: str = ""
    lineage: ArtifactLineage = Field(default_factory=ArtifactLineage)


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
    effective_goal_set: EffectiveGoalSet = Field(default_factory=EffectiveGoalSet)
    target_scope: TargetScope = Field(default_factory=TargetScope)
    goal_query_specs: list[GoalQuerySpec] = Field(default_factory=list)
    requested_goals: list[str] = Field(default_factory=list)
    authorized_goal_ids: list[str] = Field(default_factory=list)
    dropped_goals: list[dict[str, Any]] = Field(default_factory=list)
    clarification_reasons: list[dict[str, Any]] = Field(default_factory=list)
    discarded_artifact_ids: list[str] = Field(default_factory=list)


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


class PlanEdge(_DictCompatContract):
    """Directed dependency between typed execution nodes."""

    source: str = Field(default="", alias="from")
    target: str = Field(default="", alias="to")
    condition: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class GoalScopedNodeInputs(AgentEngineContract):
    goal_ids: list[str] = Field(default_factory=list)
    query_spec_id: str = ""
    target_scope_id: str = ""


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


class DeliverableResult(AgentEngineContract):
    schema_version: str = "deliverable_result.v1"
    goal_id: str = ""
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
    ]
    status: Literal["completed", "partial", "failed", "blocked"]
    payload: dict[str, Any] = Field(default_factory=dict)
    source_artifact_ids: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None


class CompositeOutputFrame(AgentEngineContract):
    schema_version: str = "composite_output_frame.v1"
    deliverables: list[DeliverableResult] = Field(default_factory=list)
    overall_status: str = "not_implemented"
    legacy_answer_variant: str = "not_implemented"


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
