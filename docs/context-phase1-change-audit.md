# Context Phase 1 Change Audit

Phase 1 introduces a deterministic context layer for multi-turn fault diagnosis. It does not migrate `goals`, does not add `task_family`, and does not rewrite the router's primary classification logic.

## Audit Summary

| File | Why changed | Main chain | SSE complete | /chat/plan | Artifact schema | Permission policy | Compatibility |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `fault_diagnosis/context/__init__.py` | Exports the new context API. | Yes | No | Yes | No | No | New facade for context package |
| `fault_diagnosis/context/contracts.py` | Defines `CaseState`, `ResolvedContext`, `PendingAction`, `ContextReference`, and compact resolved-context summary. | Yes | Yes, via summary helper | Yes | Defines snapshot version constant | No | Keeps legacy context projection shape |
| `fault_diagnosis/context/case_store.py` | Projects cases from thread artifacts and writes/reads optional `case_state_snapshot`. | Yes | Indirect | Yes | Adds `payload.case_state_snapshot.schema_version` cache | No | Falls back to raw artifact payload |
| `fault_diagnosis/context/resolver.py` | Resolves continuation/report/action/correction/ambiguous relations and enforces auth-scoped inheritance. | Yes | Indirect | Yes | No | Yes | Writes legacy `context_resolution` into payload |
| `fault_diagnosis/context/manager.py` | Public entry point for `ContextManager.resolve`. | Yes | Indirect | Yes | No | Yes through resolver | New API, no router rewrite |
| `fault_diagnosis/single_agent/context.py` | Thin compatibility wrapper around the new context layer. | Yes, legacy callers | No | Indirect | No | Uses new resolver | Yes |
| `fault_diagnosis/single_agent/stages.py` | Calls `ContextManager.resolve` after understanding and before routing. | Yes | Indirect | No | No | Uses auth context | Keeps existing stage sequence |
| `fault_diagnosis/single_agent/intent.py` | Passes `resolved_context` through existing capability decision and legacy fields. | Yes | Indirect | Yes | No | No | Keeps `TaskType`, `intent_stack`, old fields |
| `fault_diagnosis/single_agent/workflow/router.py` | Accepts optional `resolved_context` and copies context fields into `TaskRoute`. | Yes | Indirect | Yes | No | No | Primary classification functions unchanged |
| `fault_diagnosis/single_agent/workflow/contracts.py` | Adds `TaskRoute.resolved_context`. | Yes | Indirect | Yes | No | No | Old route fields retained |
| `fault_diagnosis/single_agent/contracts.py` | Adds `SingleAgentDecision.resolved_context`. | Yes | Yes | Yes | Saved inside decision payload | No | Old fields retained |
| `fault_diagnosis/single_agent/planner.py` | Adds context resolution to `/chat/plan` and outputs compact summary. | No stream side effects | No | Yes | No | Uses auth context | Plan remains side-effect-free |
| `fault_diagnosis/single_agent/output/payloads.py` | Emits compact top-level and `workflow_route.resolved_context`. | Yes | Yes | No | No | No | `decision.resolved_context` still present |
| `fault_diagnosis/single_agent/runner.py` | Adds compact `resolved_context` to trace metadata. | Yes | No field change | No | No | No | Runner only calls summary helper |
| `fault_diagnosis/single_agent/flow.py` | Carries resolved context into workflow-route artifact and finalize metadata paths. | Yes | Yes | No | Saved in artifact payload | No | Existing workflow nodes retained |
| `fault_diagnosis/single_agent/artifacts.py` | Writes optional `case_state_snapshot` cache when saving an envelope. | Yes, at save time | No | No | Yes, additive cache | No | Source of truth unchanged |
| `fault_diagnosis/single_agent/output/renderers.py` | Adds `已复位` to dangerous action completion phrases. | Yes | Indirect | No | No | No | Output contract unchanged |
| `fault_diagnosis/single_agent/evidence/quality.py` | Adds `已复位` guardrail detection. | Yes | Indirect | No | No | No | Existing guardrail result shape retained |
| `tests/test_context_manager.py` | Unit coverage for context resolution, auth, ambiguity, fallback. | No | No | No | Validates fallback | Validates no unauthorized inheritance | Test only |
| `tests/test_workorder_artifact_followup.py` | Stream/workorder follow-up assertions for resolved context and stale disclosure. | No | Validates | No | No | No | Test only |
| `tests/test_plan_endpoint.py` | Ensures plan snapshot exposes resolved context and stays side-effect-free. | No | No | Validates | No | Uses trusted auth | Test only |
| `tests/test_workflow_routing.py` | Minimal route compatibility assertions. | No | No | No | No | No | Test only |
| `tests/test_voice_exchange.py` | Aligns legacy auth assertion with server-owned admin table scope. | No | No | No | No | Yes | Test only |
| `tests/evals/agent_workflow_cases.yaml` | Aligns eval expectations with current guest denial policy. | No | No | Yes, eval | No | Yes | Eval only |
| `scripts/context_acceptance_test.py` | Real `/chat/plan` acceptance script for six multi-turn context scenarios. | No | No | Yes | Validates fallback | Validates no leak | Script only |

## Key Checks

- `single_agent/runner.py` does not contain context business rules. It only calls `summarize_resolved_context(...)` to attach compact trace metadata.
- `route_task` still runs the original task classification, requested-output, intent-stack, flags, slots and subgoal functions. It only consumes `resolved_context` to populate route context fields and report-handoff compatibility.
- `case_state_snapshot` is an additive cache with `schema_version`. If the cache is missing, malformed, version-mismatched, or inconsistent, projection falls back to the original artifact payload.
- `DiagnosisArtifactEnvelope` remains the source of truth. The case store reconstructs `CaseState` from envelope payload fields such as request, decision objects, evidence bundle, report artifact, analysis artifact, operation report payload, and workorder decision.
- Old fields are retained: `TaskType`, `intent_stack`, `context_resolution`, `relation_to_previous`, `referenced_artifact_id`, `referenced_case_id`, `evidence_mode`, `should_refresh_runtime_data`, and `report_from_previous_artifact`.

## Output Contract Impact

- SSE `complete` now includes top-level `resolved_context` as a compact summary.
- `workflow_route.resolved_context` also uses the compact summary.
- `decision.resolved_context` remains present for compatibility and may contain the fuller internal structure.
- `/chat/plan` emits compact `PlanSnapshot.resolved_context` with relation, active case, artifact/report references, inherited slots, pending action type/count, stale flag, missing context, resolution reason, and candidate counts.
- Trace metadata uses the same compact summary and intentionally omits long evidence, SQL text, and report body.

## Artifact Schema Impact

`payload.case_state_snapshot` is additive and optional:

```json
{
  "schema_version": "case_state_snapshot.v1",
  "case_id": "...",
  "thread_id": "...",
  "active_asset": "...",
  "latest_artifact_id": "..."
}
```

This snapshot is never authoritative. Projection must still work from raw envelope payloads when the snapshot is absent or invalid.

## Permission Impact

Context inheritance is checked during resolve:

- Only artifacts from the current `thread_id` are loaded.
- Explicit current-message device wins over prior context.
- Device inheritance requires `auth_context.asset_scope`, unless admin.
- Report inheritance requires `data.report.read` or `data.report.read_all`.
- Runtime-data inheritance requires a non-empty `table_scope`; SQL ACL still rechecks later.
- Unauthorized resolution does not inherit device, fault code, report, pending action, report URL, or previous artifact id, and does not expose previous device details in debug context.
