# Legacy Deprecation Phase 5.1

Phase 5.1 marks `TaskType`, `primary_task_type`, `candidate_task_types`, and `intent_stack` as deprecated compatibility fields. It does not remove them and does not change the legacy workflow policy, enabled nodes, runtime tools, SQL/RAG/report/workorder/action execution paths, SSE payloads, traces, artifacts, or eval snapshots.

## Why They Cannot Be Removed

`TaskType` and `intent_stack` are still used by existing compatibility and execution surfaces:

- workflow policy selection and conditional node resolution
- evidence-gap planning for workorder follow-up
- stage routing, output rendering, artifact typing, and planner snapshots
- planning diff, planner gate, diagnosis readiness, and high-risk dry-run checks
- frontend, SSE complete payloads, traces, artifacts, and eval assertions

Removing these fields now would combine a planning migration with public contract removal and workflow behavior changes.

## Current Role

- `TaskType` is the legacy primary workflow classifier. It is deprecated for new internal planning logic, but retained for policy, frontend, eval, artifact, trace, and SSE compatibility.
- `primary_task_type` is the serialized compatibility projection of `TaskType`.
- `candidate_task_types` is a legacy alternate task-type projection for compatibility/debug consumers.
- `intent_stack` is the legacy policy intent projection. Its source should remain `GoalSet.intent_stack_projection + legacy candidates`, with stable dedupe.

The compatibility projection is centralized in `fault_diagnosis/single_agent/compat/legacy_intent.py` for low-risk merge/sync behavior.

## New Primary Path

New planning logic should use:

`ResolvedContext -> GoalSet -> TaskFamily -> ShadowPlanner -> PlanningDiff -> PlannerGate`

These layers are the migration path for internal planning. They must not silently expand `enabled_nodes`, `runtime_tools`, write actions, workorder execution, or action execution.

## Allowed Legacy Readers

Current allowed legacy readers are limited to:

- `workflow/policies.py` and `workflow/evidence_gap.py`
- `workflow/router.py` legacy projection logic and `single_agent/compat/*`
- `single_agent/stages.py`, `flow.py`, `planner.py`, output payload/rendering, artifact compatibility, and evidence contracts
- `single_agent/planning/*` comparison, gate, readiness, and manual-confirmation logic
- frontend compatibility surfaces and tests/evals
- scripts that scan or validate the compatibility contract

Other modules should not introduce new `TaskType`, `primary_task_type`, `candidate_task_types`, or `intent_stack` execution dependencies.

## Guardrail

Run:

```bash
PYTHONPATH=. python scripts/legacy_deprecation_check.py
```

The script writes:

- `trash/run/legacy_deprecation_check.json`
- `trash/run/legacy_deprecation_check.md`

It fails when a legacy-field reference appears outside the allowlist. Documentation references are reported separately and do not count as execution dependencies.

## Removal Prerequisites

True removal is blocked until all of these are true:

- workflow policy and evidence-gap planning no longer read legacy fields
- stages, output rendering, artifacts, planner snapshots, and evals tolerate missing legacy fields
- frontend and SSE consumers have completed a compatibility window
- planner-gated coverage replaces legacy policy inputs for the approved task families
- action/workorder remains guarded by explicit human confirmation and dry-run validation

The next target is Phase 5.2 compatibility-only migration: keep public legacy fields, but progressively stop using them as internal execution inputs.

## Phase 5.2 Compatibility-Only Migration Result

Phase 5.2 reduced scattered internal reads without changing public compatibility fields or execution behavior.

Migrated internal dependencies:

- `stages.py` summary/safety prefixes now use compatibility helpers that prefer GoalSet/task-family semantics before legacy fallback.
- `workflow/evidence_gap.py` workorder follow-up detection now prefers GoalSet/action-target semantics before legacy fallback.
- `flow.py`, `planner.py`, `output/payloads.py`, `evidence/__init__.py`, and `artifacts.py` now build compatibility payload fields through `single_agent/compat/legacy_intent.py`.
- `planning/action_readiness.py`, `planning/diagnosis_readiness.py`, `planning/gate.py`, and `planning/manual_confirmation.py` now centralize legacy fallback through the compat adapter.

Deleted old code:

- Removed repeated local compatibility payload construction and direct legacy intent checks from the migrated modules.
- No public schema fields, SSE fields, artifact fields, trace fields, eval fields, or workflow policy entries were removed.

Current scan movement from the Phase 5.1 baseline:

- `TaskType` read/write files: `43/41` -> `33/33`
- `intent_stack` read/write files: `27/25` -> `20/20`
- policy dependency files: `13` -> `7`
- `disallowed_dependency_hits`: remains `0`

Remaining deletion blockers:

- `workflow/policies.py` still consumes legacy task and intent fields for execution.
- `workflow/router.py` still generates compatibility fields.
- public contracts, output templates/renderers, planner comparison contracts, tests/evals, and artifact compatibility still expose or assert legacy fields.

Next step: Phase 5.3 should focus on workflow policy migration. It should move policy selection and conditional node resolution toward GoalSet/task-family/readiness inputs while continuing to emit the public legacy fields.

## Phase 5.3 Workflow Policy Migration Result

Phase 5.3 moved the workflow policy selector and node-resolution logic toward GoalSet/task-family axes while keeping legacy execution fallback.

Migrated internal dependencies:

- `workflow/policies.py` now selects through `select_policy_from_intent_axes(route)`, using task family, GoalSet goal types, resolved context, and readiness-style action/workorder fields first.
- `resolve_nodes_from_goals(route)` replaces the duplicated `intent_stack` branch for SQL, knowledge, analysis, report, recommendation, workorder, permission, risk, and audit nodes.
- If GoalSet/task-family axes disagree with the legacy policy selection, execution falls back to the legacy policy unless the result is already proven safe.
- `planning/diff_evaluator.py`, `planning/gate.py`, and `planning/shadow_planner.py` now obtain deprecated compatibility fields through `single_agent/compat/legacy_intent.py`.
- `workflow/goals.py` and `workflow/task_family.py` no longer import the `TaskType` enum just to normalize compatibility values.

Deleted or reduced old code:

- Removed the old `_nodes_required_by_intents` branch in `workflow/policies.py`.
- Removed the unused `_missing` helper from `planning/diff_evaluator.py`.
- Removed repeated planning legacy-field merge/projection logic from planning modules and centralized it in the compat adapter.

Current scan movement from the Phase 5.2 baseline:

- `TaskType` read/write files: `33/33` -> `27/28`
- `intent_stack` read/write files: `20/20` -> `15/15`
- policy dependency files: `7` -> `1`
- `disallowed_dependency_hits`: remains `0`

Remaining deletion blockers:

- `workflow/policies.py` still retains `TaskType` policy registry and legacy fallback.
- `workflow/router.py` still generates compatibility fields.
- public contracts, output templates/renderers, planner/gate contracts, tests/evals, and artifact compatibility still expose or assert legacy fields.

Next step: Phase 5.4 can plan internal legacy removal locally, but public fields still cannot be removed. The safest next target is workflow policy fallback reduction after more eval coverage proves GoalSet/task-family policy parity.
