# TaskFamily Phase 3 Change Audit

Phase 3 adds `task_family` compatibility mapping and observability output. It does not change workflow policy, node selection, tool selection, or execution stages.

## New And Modified Files

| File | Change |
| --- | --- |
| `fault_diagnosis/single_agent/workflow/contracts.py` | Adds `TaskFamily`, `TaskFamilyResolution`, and task-family fields on `TaskRoute`. |
| `fault_diagnosis/single_agent/workflow/task_family.py` | Adds deterministic TaskType-to-TaskFamily mapping and fallback handling. |
| `fault_diagnosis/single_agent/workflow/router.py` | Resolves task family after TaskType, GoalSet, and merged `intent_stack` are available. |
| `fault_diagnosis/single_agent/workflow/__init__.py` | Exports task-family contracts and helper. |
| `fault_diagnosis/single_agent/contracts.py` | Adds task-family fields to `SingleAgentDecision`. |
| `fault_diagnosis/single_agent/intent.py` | Copies route task-family fields into decisions. |
| `fault_diagnosis/single_agent/planner.py` | Adds task family to `/chat/plan` snapshot, intent axes, and workflow route. |
| `fault_diagnosis/single_agent/output/payloads.py` | Adds top-level complete `task_family` and route task-family fields where route output already exists. |
| `fault_diagnosis/single_agent/flow.py` | Adds task-family fields to workflow-route artifact and direct fast-path decision. |
| `fault_diagnosis/single_agent/runner.py` | Adds compact task-family fields to trace metadata. |
| `tests/test_task_family.py` | Adds mapping, fallback, mismatch, and enum-stability tests. |
| `tests/test_workflow_routing.py` | Adds task-family route/decision assertions. |
| `tests/test_plan_endpoint.py` | Adds plan task-family assertions. |
| `tests/evals/evaluators.py` | Supports `expected.task_family` and `expected.route.task_family`. |
| `tests/evals/agent_workflow_cases.yaml` | Adds representative task-family eval assertions. |
| `scripts/context_acceptance_test.py` | Adds minimal task-family acceptance assertions. |
| `scripts/goal_acceptance_test.py` | Adds minimal task-family acceptance assertions. |

## Output Field Impact

- `SingleAgentDecision` now includes `task_family`, `task_family_reason`, `task_family_source`, and `task_family_warnings`.
- `/chat/plan` now includes top-level `task_family`, `task_family_reason`, and `task_family_source`.
- `PlanSnapshot.intent_axes` and `PlanSnapshot.workflow_route` include `task_family`.
- SSE `complete` includes top-level `task_family`.
- SSE `workflow_route` includes task-family fields only on complete payloads that already had `workflow_route`.

## SSE Complete Impact

Direct lightweight replies keep the old compact shape and do not gain a forced `workflow_route`. They only add top-level `task_family = "meta"` and the same field inside `decision`.

Report-handoff and full diagnosis completes add task-family fields to their existing `workflow_route`.

## Trace Impact

Trace metadata includes compact task-family fields:

- `task_family`
- `task_family_reason`
- `task_family_source`
- `task_family_warnings`

These fields contain no SQL, evidence body, report body, or raw tool payload.

## Artifact Impact

The `workflow_route` artifact includes:

- `task_family`
- `task_family_reason`
- `task_family_source`
- `task_family_warnings`

Diagnosis artifacts also keep the additive fields through `decision.model_dump()`.

## Behavior Compatibility

- Workflow policy is unchanged.
- Runtime tool selection is unchanged.
- SQL, RAG, report, and workorder execution stages are unchanged.
- `TaskType` is retained.
- `intent_stack` is retained.
- `context_resolution`, `resolved_context`, and `goal_set` are retained.
- Unknown task types do not introduce a public `unknown` family; they fall back to an existing public family with debug warnings.
