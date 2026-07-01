# Phase 4.4.2 Diagnosis Limited Active For Explanation-Only

Phase 4.4.2 allows a very small subset of diagnosis tasks to select `planner_gated`, but only for read-only explanation workflows. It does not migrate action or workorder behavior.

## Goal

The goal is to test whether planner-gated execution can safely narrow low-risk diagnosis explanation paths after the Phase 4.4.1 dry-run readiness signal.

Eligible task modes are limited to:

- `alarm_triage`
- `fault_diagnosis`

Default blocked modes:

- `root_cause_analysis`
- `health_assessment`

RCA and health assessment remain conservative because they require stronger causal, temporal, trend, and sufficiency checks before planner-gated active execution is safe.

## Configuration

New controls:

- `PLANNER_GATE_ENABLE_DIAGNOSIS_ACTIVE=false`
- `PLANNER_GATE_DIAGNOSIS_ACTIVE_MODES=alarm_triage,fault_diagnosis`
- `PLANNER_GATE_DIAGNOSIS_ACTIVE_REQUIRE_READINESS=candidate_for_limited_active`
- `PLANNER_GATE_DIAGNOSIS_ACTIVE_MAX_DIFF_SEVERITY=warning`
- `PLANNER_GATE_DIAGNOSIS_ACTIVE_ALLOW_RCA=false`
- `PLANNER_GATE_DIAGNOSIS_ACTIVE_ALLOW_HEALTH=false`

Default behavior remains legacy execution. Diagnosis active requires explicit opt-in plus all readiness and safety checks.

## Active Scope

Planner-gated diagnosis active may only project:

- `sql`
- `knowledge`
- `analysis`
- `resolution_recommendation`
- `report`, only when report output or a report goal is explicit

It must not enable:

- `workorder_decision`
- action execution
- device control
- configuration writes

Safety nodes already enabled by legacy policy must be preserved. Planner-gated diagnosis cannot remove `permission_check`, `risk_check`, `audit_log`, `evidence_validation`, or `output_guardrail`.

## Diagnosis Readiness

`diagnosis_readiness` now carries:

- `ready_for_active`
- `active_allowed`
- `active_mode`
- `active_scope`
- `active_blockers`
- `evidence_complete`
- `has_runtime_status`
- `has_manual_reference`
- `has_alarm_or_fault_context`
- `claims_have_supporting_evidence`
- `stale_evidence_disclosed`
- `missing_critical_evidence`
- `diagnosis_mode`
- `recommended_next_phase`

`ready_for_active=true` only means limited diagnosis explanation active is allowed. It does not authorize workorders, device actions, or high-risk conclusions.

## Blocked Cases

Planner gate falls back to `legacy_policy` when any condition fails, including:

- planning diff not `aligned` or `acceptable_diff`
- diff severity above `warning`
- critical diff
- stale evidence without disclosure
- missing runtime status
- missing manual reference for alarm/fault explanation or recommendations
- claims without supporting evidence
- missing critical evidence
- ambiguous context
- unauthorized inherited artifact
- action follow-up context
- decide-workorder or high-risk goals
- shadow tools exceeding legacy runtime tools
- safety node removal
- workorder node activation
- output semantics claiming execution, dispatch, reset, stop, or parameter changes

## Action And Workorder Boundary

Action and workorder remain fully blocked in this phase. The system must not automatically create, dispatch, close, execute, or complete a workorder. It must not reset, stop, start, or modify devices.

Phase 4.4.3 can introduce workorder dry-run design, but that is separate from diagnosis explanation active.
