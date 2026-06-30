# Policy Diff Phase 4.2 Change Audit

Phase 4.2 adds a deterministic policy diff evaluator for observation and eval gates. It does not change real workflow execution.

## New And Modified Files

| File | Change |
| --- | --- |
| `fault_diagnosis/single_agent/planning/diff_contracts.py` | Adds PlanningDiff and typed diff contracts. |
| `fault_diagnosis/single_agent/planning/diff_evaluator.py` | Adds deterministic legacy-vs-shadow comparison. |
| `fault_diagnosis/single_agent/planning/diff_summaries.py` | Adds compact summary builder for output surfaces. |
| `fault_diagnosis/single_agent/contracts.py` | Adds `SingleAgentDecision.planning_diff_summary`. |
| `fault_diagnosis/single_agent/planner.py` | Adds compact planning diff to `/chat/plan`. |
| `fault_diagnosis/single_agent/flow.py` | Builds planning diff after shadow planning and emits compact summaries. |
| `fault_diagnosis/single_agent/output/payloads.py` | Adds compact planning diff to SSE complete payloads. |
| `fault_diagnosis/single_agent/runner.py` | Adds compact planning diff to trace metadata. |
| `tests/test_planning_diff.py` | Adds evaluator unit coverage. |
| `scripts/planning_diff_acceptance_test.py` | Adds Phase 4.2 acceptance checks. |
| `tests/evals/evaluators.py` | Adds `expected.planning_diff` assertions. |
| `tests/evals/agent_workflow_cases.yaml` | Adds representative planning diff eval gates. |

## Output Impact

`/chat/plan`, SSE `complete`, and trace metadata expose compact `planning_diff` only. Compact summaries include status, severity, counts, short summary, diff type names, and conservative migration readiness.

Full diff lists are not returned in `/chat/plan`, SSE `complete`, or trace metadata.

## Artifact Impact

Runtime artifacts only carry compact planning diff summaries in the current trace-backed output path. This avoids leaking full diff lists through complete payload trace data.

## Policy And Runtime Impact

Workflow policy is unchanged.

`enabled_nodes` are unchanged.

`runtime_tools` are unchanged.

`planning_diff` is not written to route flags, decision flags, workflow policy metadata, node resolver inputs, or runtime tool selection paths.

## Safety Boundary

The evaluator marks critical mismatches for unauthorized shadow runtime tools, missing safety guardrail nodes, stale workorder/action output without refresh or disclosure, completed action semantics, and unauthorized context references.

`needs_review` is allowed during Phase 4.2 and is reported for Phase 4.3 analysis. Acceptance fails only on critical safety issues, shadow authorized tools exceeding legacy runtime tools, execution-field mutation, unauthorized references, or stale workorder without refresh/disclosure.
