# Legacy TaskType / intent_stack Removal Readiness

Phase 4.4R does not remove `TaskType`, `primary_task_type`, or `intent_stack`. It only records the dependency surface that must be retired before removal.

## Why They Cannot Be Removed Now

`TaskType` / `primary_task_type` and `intent_stack` are still execution inputs, not compatibility-only outputs.

They are still used by:

- legacy workflow policy selection
- evidence gap and follow-up planning
- stage routing and workorder handling
- planner diff comparison against legacy policy
- planner gate fallback and diagnosis eligibility checks
- test/eval expectations
- SSE, trace metadata, and artifact-compatible payloads

Removing them now would change routing, evidence collection, workorder safety, and frontend/debug output at the same time.

## Current Dependency Classes

Run:

```bash
PYTHONPATH=. python scripts/legacy_dependency_scan.py
```

The scan writes:

- `trash/run/legacy_dependency_scan.json`
- `trash/run/legacy_dependency_scan.md`

The scan groups dependencies into:

- TaskType readers and writers
- `intent_stack` readers and writers
- test/eval dependencies
- frontend dependencies
- artifact/schema dependencies
- workflow and policy logic dependencies

Expected current policy dependencies include:

- `fault_diagnosis/single_agent/workflow/policies.py`
- `fault_diagnosis/single_agent/workflow/evidence_gap.py`
- `fault_diagnosis/single_agent/stages.py`
- `fault_diagnosis/single_agent/planner.py`
- `fault_diagnosis/single_agent/planning/*`

## Conditions Before Deletion

Deletion is not safe until all of these are true:

- workflow policy no longer reads `TaskType`, `primary_task_type`, or `intent_stack`
- evals no longer assert legacy fields as core behavior
- frontend progress rendering no longer depends on legacy task fields
- artifact readers tolerate missing legacy task fields
- planner-gated active coverage is broad enough for non-action task families
- action/workorder remains guarded by explicit human confirmation
- trace and SSE consumers have a compatibility window for older fields

## Recommended Removal Path

1. Deprecate

Mark `TaskType`, `primary_task_type`, and `intent_stack` as deprecated, but keep producing them in SSE, trace, eval snapshots, and artifacts.

2. Compatibility-only

Stop using them as execution inputs. Keep them as derived output fields for old clients and historical artifacts.

3. Remove internal dependency

Remove internal reads from workflow policy, planner diff, gate logic, and tests. Keep external artifact/SSE compatibility fields for a fixed migration window.

## Short-Term Rule

Do not remove these fields from SSE or artifacts in the short term. The safe first move is to stop internal execution dependency, not to delete externally visible fields.
