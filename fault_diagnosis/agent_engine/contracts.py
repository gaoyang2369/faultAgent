"""Agent Engine V2 sidecar contracts.

These models are intentionally independent from the legacy single-agent
runtime. Phase 1 only defines serializable contracts for later plan-only,
shadow, and runtime work.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ENGINE_VERSION = "v2"
PLAN_SNAPSHOT_V2_SCHEMA_VERSION = "agent_engine_plan_snapshot.v2"

NodeStatus = Literal["pending", "running", "completed", "skipped", "blocked", "failed", "cancelled"]
PlanSnapshotStatus = Literal["not_implemented", "planned", "validated", "blocked", "failed"]
RiskLevel = Literal["low", "medium", "high", "critical"]


class AgentEngineContract(BaseModel):
    """Base model for V2 contracts."""

    model_config = ConfigDict(extra="forbid")


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


class ExecutionPlan(AgentEngineContract):
    """Validated or candidate plan shape consumed by the future V2 runtime."""

    schema_version: str = "execution_plan.v1"
    plan_id: str = ""
    plan_version: str = "v2.empty"
    goals: list[dict[str, Any]] = Field(default_factory=list)
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = "low"
    interrupts: list[dict[str, Any]] = Field(default_factory=list)
    approval_requirements: list[dict[str, Any]] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    fallbacks: list[dict[str, Any]] = Field(default_factory=list)


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


class PlanSnapshotV2(AgentEngineContract):
    """Side-effect-free V2 plan snapshot for internal tests and future API projection."""

    schema_version: str = PLAN_SNAPSHOT_V2_SCHEMA_VERSION
    engine_version: str = ENGINE_VERSION
    status: PlanSnapshotStatus = "not_implemented"
    intent_frame: IntentFrame = Field(default_factory=IntentFrame)
    rewrite_frame: RewriteFrame = Field(default_factory=RewriteFrame)
    context_frame: ContextFrame = Field(default_factory=ContextFrame)
    skill_route: SkillRoute = Field(default_factory=SkillRoute)
    execution_plan: ExecutionPlan = Field(default_factory=ExecutionPlan)
    node_results: list[NodeResult] = Field(default_factory=list)
    evidence_ledger: EvidenceLedger = Field(default_factory=EvidenceLedger)
    output_frame: OutputFrame = Field(default_factory=OutputFrame)
    trace: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
