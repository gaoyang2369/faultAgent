# Legacy TaskType / intent_stack Removal Readiness

Current status: `workflow policy migration in progress`.

Phase 5.1 does not remove `TaskType`, `primary_task_type`, `candidate_task_types`, or `intent_stack`. These fields are now deprecated compatibility fields, but they are still required by existing execution and public-output surfaces.

## Why They Cannot Be Removed Now

`TaskType` / `primary_task_type` and `intent_stack` are still used by:

- legacy workflow policy selection and conditional node resolution
- evidence gap and workorder follow-up planning
- stage routing, planner snapshots, output rendering, and artifact typing
- planning diff comparison, planner gate fallback, diagnosis readiness, and high-risk dry-run checks
- test/eval expectations
- SSE, trace metadata, frontend-compatible payloads, and artifact-compatible schemas

Removing them now would change routing, evidence collection, workorder safety, and frontend/debug output at the same time.

## Current Dependency Checks

Run:

```bash
PYTHONPATH=. python scripts/legacy_dependency_scan.py
PYTHONPATH=. python scripts/legacy_deprecation_check.py
```

The scans write:

- `trash/run/legacy_dependency_scan.json`
- `trash/run/legacy_dependency_scan.md`
- `trash/run/legacy_deprecation_check.json`
- `trash/run/legacy_deprecation_check.md`

`legacy_dependency_scan.py` records the full dependency surface. `legacy_deprecation_check.py` fails when a new non-allowlisted internal dependency appears outside compatibility paths.

## Remaining Removal Blockers

Deletion is blocked while these modules still read legacy fields:

- `fault_diagnosis/single_agent/workflow/policies.py`
- `fault_diagnosis/single_agent/workflow/router.py`
- `fault_diagnosis/single_agent/planning/*` contracts and planner comparison outputs
- `fault_diagnosis/single_agent/output/*` public compatibility outputs
- artifact, frontend, and eval compatibility surfaces

## Deprecated-But-Retained Role

- `TaskType`: legacy primary workflow classifier; deprecated for new internal planning logic, retained for policy/frontend/eval/artifact compatibility.
- `primary_task_type`: serialized compatibility projection of `TaskType`.
- `candidate_task_types`: legacy alternate task-type compatibility projection.
- `intent_stack`: legacy policy intent projection; source should be `GoalSet.intent_stack_projection + legacy candidates`.

The low-risk projection and sync helpers live in `fault_diagnosis/single_agent/compat/legacy_intent.py`.

## Recommended Removal Path

1. Deprecation phase

Mark legacy fields as deprecated, keep producing them, centralize low-risk compatibility projection, and block new dependency growth.

2. Compatibility-only phase

Keep public fields for old clients and historical artifacts, but migrate internal execution inputs away from them.

3. Removal phase

Remove internal reads only after workflow policy, evidence-gap planning, stages, planner comparison/gate logic, evals, frontend, and artifact readers tolerate missing legacy fields.

## Phase 5.2 Compatibility-Only Migration Result

Phase 5.2 reduced direct internal reads while keeping all public fields and execution behavior stable.

Migrated:

- workorder follow-up detection outside policy now prefers GoalSet/action-target semantics.
- final-answer summary and high-risk safety helpers now use compat adapter helpers instead of scattered legacy reads.
- plan, flow, evidence, artifact, and complete-payload compatibility fields are projected through `single_agent/compat/legacy_intent.py`.
- planner readiness/gate helpers now centralize legacy fallback through the compat adapter.

Removed:

- repeated local compatibility route-field dictionaries
- repeated direct legacy intent checks in migrated modules

Not removed:

- public `TaskType`, `primary_task_type`, `candidate_task_types`, or `intent_stack` fields
- SSE complete, `/chat/plan`, artifact, trace, or eval compatibility outputs
- workflow policy legacy execution reads

Scan result:

- `TaskType` read/write files: `43/41` -> `33/33`
- `intent_stack` read/write files: `27/25` -> `20/20`
- policy dependency files: `13` -> `7`
- `disallowed_dependency_hits`: `0`

## Next Phase

Phase 5.3 should target workflow policy migration. It should move policy selection and conditional node resolution away from legacy task/intent fields while continuing to emit public compatibility fields until downstream consumers no longer require them.

## Phase 5.3 Workflow Policy Migration Result

Phase 5.3 reduced workflow policy dependency on `TaskType` / `intent_stack` as primary execution inputs.

Migrated:

- policy selection now enters through `select_policy_from_intent_axes(route)`, preferring task family, GoalSet goal types, resolved context, and action/workorder readiness-style fields.
- node resolution now uses `resolve_nodes_from_goals(route)` for SQL, knowledge, analysis, report, recommendation, workorder, permission, risk, and audit nodes.
- planning diff, shadow planner, and planner gate compatibility projections now route through `single_agent/compat/legacy_intent.py`.
- GoalSet and task-family helpers no longer import the legacy `TaskType` enum solely for value normalization.

Removed:

- old duplicated `intent_stack` node-resolution branch in `workflow/policies.py`
- unused planning diff `_missing` helper
- repeated planning legacy projection/merge code outside the compat adapter

Not removed:

- public `TaskType`, `primary_task_type`, `candidate_task_types`, or `intent_stack` fields
- SSE complete, `/chat/plan`, artifact, trace, or eval compatibility outputs
- `workflow/policies.py` legacy policy registry and fallback path
- action/workorder dry-run and manual confirmation guardrails

Scan result from Phase 5.2 baseline:

- `TaskType` read/write files: `33/33` -> `27/28`
- `intent_stack` read/write files: `20/20` -> `15/15`
- policy dependency files: `7` -> `1`
- `disallowed_dependency_hits`: `0`

Remaining blockers before true removal:

- `workflow/policies.py` still owns the legacy policy registry and must keep fallback behavior while parity coverage grows.
- `workflow/router.py` still generates compatibility projections.
- public contracts, output renderers/templates, planner/gate contracts, tests/evals, and artifact compatibility still require the fields.

Next phase: Phase 5.4 can start localized internal legacy removal planning, but public schema removal is still blocked. The immediate safe target is reducing the workflow policy fallback path only where evals prove no enabled-node or runtime-tool expansion.

## Phase 5.4 Internal Legacy Removal Result

Phase 5.4 moved remaining low-risk internal readers behind compatibility helpers and clarified the scan into internal execution metrics versus compatibility assertions.

Migrated:

- output rendering, security authorization, and local dev mode now use `single_agent/compat/legacy_intent.py` for deprecated task values.
- workflow policy node resolution continues to use GoalSet/task-family axes and no longer uses direct legacy intent fields in the main resolver.
- planner/diff/gate compatibility projections remain centralized through the compat adapter.
- `legacy_dependency_scan.py` primary read/write counts now exclude tests/evals/scripts and report them separately as compatibility dependencies.

Removed:

- direct `primary_task_type` reads from renderer/security/dev-mode execution helpers
- enum-based SQL conditional checks in the policy node resolver

Not removed:

- public `TaskType`, `primary_task_type`, `candidate_task_types`, or `intent_stack`
- SSE complete, `/chat/plan`, artifact, trace, or eval compatibility outputs
- legacy artifact compatibility reads
- action/workorder dry-run and manual-confirmation guardrails

Scan result from Phase 5.3 baseline:

- Internal `TaskType` read/write files: `27/28` -> `10/3`
- Internal `intent_stack` read/write files: `15/15` -> `5/2`
- All compatibility references, including tests/evals/scripts: `TaskType 25/27`, `intent_stack 16/15`
- policy dependency files: `1` -> `1`
- `disallowed_dependency_hits`: `0`

Remaining blockers before true removal:

- `workflow/policies.py` still keeps the legacy policy registry and fallback/parity check.
- `workflow/router.py` still creates public compatibility projections.
- public contracts, output templates/contracts, planning/gate contracts, tests/evals, and artifact compatibility still require legacy fields.

Next phase: Phase 5.5 can prepare public compatibility stabilization and removal criteria. It should not remove fields yet; it should prove consumers can tolerate compatibility-only fields becoming optional or separately versioned.

## Phase 5.5 Remove Legacy Execution Fallbacks Result

Phase 5.5 removed the remaining legacy execution fallback from workflow policy selection.

Removed execution fallback:

- `workflow/policies.py` no longer imports or reads `TaskType`, `primary_task_type`, `candidate_task_types`, or `intent_stack`.
- `select_policy_from_intent_axes(route)` no longer performs legacy parity checks or legacy policy fallback.
- policy selection now uses only `task_family`, GoalSet goal types, requested output, resolved context, and action/workorder fields.
- workorder node resolution no longer calls legacy intent compatibility helpers; it uses GoalSet workorder goals and `action_target`.

Still retained as public compatibility output:

- `TaskType`
- `primary_task_type`
- `candidate_task_types`
- `intent_stack`
- `/chat/plan`, SSE complete payloads, artifacts, traces, eval snapshots, and frontend-compatible route payloads

Compatibility-only status:

- `policy_dependency_files`: `0`
- Internal `TaskType` read/write files: `10/3` -> `1/1`
- Internal `intent_stack` read/write files: `5/2` -> `1/1`
- Remaining internal read/write file: `workflow/router.py`, where the fields are generated as public compatibility projection.
- Full compatibility references remain in public contracts, output schemas, tests/evals, and artifact-compatible surfaces.

Removal is still blocked for public schema:

- `workflow/router.py` still generates compatibility projections.
- public contracts and historical artifacts still expose legacy fields.
- output/eval/frontend-compatible surfaces still assert or display legacy fields.

Next phase: Phase 5.6 can open the public schema deprecation window. It should define versioned payload behavior, optional-field tolerance, frontend/eval migration criteria, and artifact backward-compatibility rules before any public field removal.
