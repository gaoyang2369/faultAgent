# Phase 4.4.1 Change Audit

## Changed Files

Added:

- `fault_diagnosis/single_agent/planning/diagnosis_readiness.py`
- `tests/test_diagnosis_dry_run_gate.py`
- `scripts/diagnosis_dry_run_acceptance_test.py`
- `docs/diagnosis-dry-run-phase4-4-1.md`
- `docs/diagnosis-dry-run-phase4-4-1-change-audit.md`

Modified:

- `fault_diagnosis/config.py`
- `fault_diagnosis/single_agent/planning/gate.py`
- `fault_diagnosis/single_agent/planning/gate_contracts.py`
- `fault_diagnosis/single_agent/planning/__init__.py`
- `fault_diagnosis/single_agent/planner.py`
- `fault_diagnosis/single_agent/output/payloads.py`
- `fault_diagnosis/single_agent/runner.py`
- `tests/evals/evaluators.py`
- `tests/evals/agent_workflow_cases.yaml`
- `tests/test_planner_gate.py`
- `tests/test_intent_context_behavior_invariants.py`
- `scripts/planner_gate_acceptance_test.py`

## Output Impact

The compact `diagnosis_readiness` summary is now emitted in:

- `/chat/plan`
- SSE complete payloads
- `workflow_route`
- trace metadata
- `planner_gate.diagnosis_readiness`

The compact output excludes long evidence, SQL text, report content, report URLs, and unauthorized device or fault-code details.

## Execution Impact

Diagnosis dry-run does not change real execution.

- `selected_execution_source` remains `legacy_policy`.
- `enabled_nodes` remain the legacy policy projection.
- `runtime_tools` remain the legacy authorization-filtered tools.
- SQL, RAG, analysis, report, workorder, evidence validation, and output guardrail stages remain on the legacy path.

Action and workorder migration remains blocked by `action_or_workorder_not_migrated`.

## Readiness Summary

Latest acceptance-script observation:

- `alarm_triage`: candidate for limited active, but `ready_for_active=false`.
- `fault_diagnosis`: candidate for limited active, but `ready_for_active=false`.
- `root_cause_analysis`: `more_eval`.
- `health_assessment`: `more_eval`.
- stale, ambiguous, unauthorized, and action/workorder cases: `keep_legacy`.

No case sets `ready_for_active=true`.

## Validation

Latest verification:

- `PYTHONPATH=. pytest -q`: 207 passed.
- `PYTHONPATH=. python tests/evals/run_plan_eval.py`: 42/42 passed.
- `PYTHONPATH=. python scripts/context_acceptance_test.py`: 6 passed.
- `PYTHONPATH=. python scripts/goal_acceptance_test.py`: 5 passed.
- `PYTHONPATH=. python scripts/shadow_planner_acceptance_test.py`: 5 passed.
- `PYTHONPATH=. python scripts/planning_diff_acceptance_test.py`: 7 passed.
- `PYTHONPATH=. python scripts/planner_gate_acceptance_test.py`: 11 passed.
- `PYTHONPATH=. python scripts/diagnosis_dry_run_acceptance_test.py`: 8 passed.
- `git diff --check`: clean.
