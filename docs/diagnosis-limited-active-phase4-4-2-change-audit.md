# Phase 4.4.2 Change Audit

## Changed Files

Added:

- `tests/test_diagnosis_limited_active_gate.py`
- `scripts/diagnosis_limited_active_acceptance_test.py`
- `docs/diagnosis-limited-active-phase4-4-2.md`
- `docs/diagnosis-limited-active-phase4-4-2-change-audit.md`

Modified:

- `fault_diagnosis/config.py`
- `fault_diagnosis/single_agent/planning/diagnosis_readiness.py`
- `fault_diagnosis/single_agent/planning/gate.py`
- `fault_diagnosis/single_agent/planning/gate_contracts.py`
- `tests/evals/evaluators.py`
- `tests/evals/agent_workflow_cases.yaml`
- `scripts/diagnosis_dry_run_acceptance_test.py`
- `docs/refactor-final-summary.md`
- `docs/diagnosis-dry-run-phase4-4-1.md`

## Configuration

New active controls:

- `PLANNER_GATE_DIAGNOSIS_ACTIVE_MODES`
- `PLANNER_GATE_DIAGNOSIS_ACTIVE_REQUIRE_READINESS`
- `PLANNER_GATE_DIAGNOSIS_ACTIVE_MAX_DIFF_SEVERITY`
- `PLANNER_GATE_DIAGNOSIS_ACTIVE_ALLOW_RCA`
- `PLANNER_GATE_DIAGNOSIS_ACTIVE_ALLOW_HEALTH`

`PLANNER_GATE_ENABLE_DIAGNOSIS_ACTIVE` remains default `false`.

## Output Surface

Compact `diagnosis_readiness` now includes:

- `diagnosis_mode`
- `ready_for_active`
- `active_allowed`
- `active_mode`
- `active_scope`
- `active_blocker_count`
- `missing_critical_evidence_count`
- `recommended_next_phase`

`planner_gate` now includes `active_scope`.

## Runtime Impact

Default runtime behavior is unchanged:

- `selected_execution_source=legacy_policy`
- `primary_task_type` unchanged
- `intent_stack` unchanged
- `enabled_nodes` unchanged
- `runtime_tools` unchanged

When explicitly enabled and all strict checks pass, diagnosis limited active may select `planner_gated` only for explanation scope. The final tools remain an intersection of shadow-authorized tools, legacy runtime tools, the hard whitelist, and authorization-filtered runtime tools.

## Active Boundary

Allowed active modes:

- `alarm_triage`
- `fault_diagnosis`

Default blocked:

- `root_cause_analysis`
- `health_assessment`
- `action_or_workorder`
- `decide_workorder`

Safety guardrails:

- no runtime tool expansion
- no workorder node
- no action execution semantics
- no critical diff
- no safety node removal
- no unauthorized inheritance

## Validation

- `PYTHONPATH=. pytest -q`: 223 passed.
- `PYTHONPATH=. python tests/evals/run_plan_eval.py`: 42/42 passed.
- `PYTHONPATH=. python scripts/context_acceptance_test.py`: 6 passed.
- `PYTHONPATH=. python scripts/goal_acceptance_test.py`: 5 passed.
- `PYTHONPATH=. python scripts/shadow_planner_acceptance_test.py`: 5 passed.
- `PYTHONPATH=. python scripts/planning_diff_acceptance_test.py`: 7 passed.
- `PYTHONPATH=. python scripts/planner_gate_acceptance_test.py`: 11 passed.
- `PYTHONPATH=. python scripts/diagnosis_dry_run_acceptance_test.py`: 8 passed, `ready_for_active_count=0`.
- `PYTHONPATH=. python scripts/diagnosis_limited_active_acceptance_test.py`: 10 passed, `active_allowed_count=2`, `ready_for_active_count=2`.
- `PYTHONPATH=. python scripts/planner_gate_observation_report.py`: 126 observations, `selected_planner_gated_count=13`, `runtime_tools_expanded_count=0`, `critical_diff_count=0`.
- `git diff --check`: clean.
