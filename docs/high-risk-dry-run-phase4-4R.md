# Phase 4.4R High-Risk Dry-Run And Legacy Deletion Readiness

Phase 4.4R accelerates the migration plan without deleting legacy execution fields and without enabling action/workorder active execution.

## Scope

This phase does:

- close and re-validate diagnosis limited active
- add workorder/action dry-run readiness
- add manual confirmation requirements
- scan dependencies before deleting `TaskType` / `primary_task_type` / `intent_stack`

This phase does not:

- delete `TaskType`
- delete `intent_stack`
- delete legacy workflow policy
- enable action/workorder active execution
- dispatch, close, reset, stop, start, or modify anything automatically

## Diagnosis Limited Active

The active diagnosis scope remains limited to:

- `alarm_triage`
- `fault_diagnosis`

Blocked by default:

- `root_cause_analysis`
- `health_assessment`
- action/workorder

Planner-gated diagnosis still requires strict readiness, aligned or acceptable planning diff, no critical diff, no runtime-tool expansion, no workorder node, preserved safety nodes, and evidence-backed claims.

## Workorder / Action Readiness

`WorkorderActionReadiness` is dry-run-only:

- `ready_for_active=false`
- `dry_run_only=true`
- workorder decision and workorder draft remain legacy
- device actions are always blocked
- stale evidence requires refresh or disclosure
- no permission/risk/audit/output guardrail removal

The compact output is available in:

- `/chat/plan.workorder_action_readiness`
- `planner_gate.workorder_action_readiness`
- SSE complete top-level `workorder_action_readiness`
- `workflow_route.workorder_action_readiness`
- trace metadata

## Manual Confirmation

`ManualConfirmationRequirement` records the required human gate:

- workorder/action requests have `required=true`
- current allowed next steps are `draft_only`, `ask_confirmation`, `refresh_data_first`, or `deny`
- dispatch, reset, stop-machine, and parameter-change requests cannot execute
- forbidden completed-action phrases are carried as a contract for output guardrails and tests

## Legacy Deletion Readiness

`scripts/legacy_dependency_scan.py` scans current dependencies and writes:

- `trash/run/legacy_dependency_scan.json`
- `trash/run/legacy_dependency_scan.md`

The current readiness conclusion is:

- `can_delete_task_type_now=false`
- `can_delete_intent_stack_now=false`

Short-term removal is unsafe because workflow policy, planner diff, gate logic, tests/evals, SSE output, and artifact-compatible payloads still consume the fields.

## Validation

Representative validation:

- `tests/test_workorder_action_dry_run.py`
- `tests/test_manual_confirmation_contract.py`
- `tests/test_legacy_dependency_scan.py`
- `scripts/high_risk_dry_run_acceptance_test.py`
- `scripts/legacy_dependency_scan.py`

The required final validation set is listed in `docs/refactor-final-summary.md`.
