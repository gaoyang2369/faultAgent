# Planner Gate Phase 4.3 Change Audit

Phase 4.3 adds read-only planner-gated execution preview. Default behavior stays legacy.

## New And Modified Files

| File | Change |
| --- | --- |
| `fault_diagnosis/config.py` | Adds planner gate config flags. |
| `fault_diagnosis/single_agent/planning/gate_contracts.py` | Adds `PlannerGateDecision`. |
| `fault_diagnosis/single_agent/planning/gate.py` | Adds deterministic gate, compact summary, and active adapter. |
| `fault_diagnosis/single_agent/contracts.py` | Adds `SingleAgentDecision.planner_gate_summary`. |
| `fault_diagnosis/single_agent/planner.py` | Adds compact gate to `/chat/plan` and plan preview. |
| `fault_diagnosis/single_agent/flow.py` | Builds gate after shadow/diff and applies active projection only when eligible. |
| `fault_diagnosis/single_agent/output/payloads.py` | Adds compact gate to complete payloads. |
| `fault_diagnosis/single_agent/runner.py` | Adds compact gate to trace metadata. |
| `tests/test_planner_gate.py` | Adds gate unit coverage. |
| `scripts/planner_gate_acceptance_test.py` | Adds Phase 4.3 acceptance scenarios. |
| `tests/evals/evaluators.py` | Adds `expected.planner_gate` assertions and default legacy-source assertion. |

## Default Behavior

Default config:

- `ENABLE_PLANNER_GATED_EXECUTION=false`
- `PLANNER_GATED_DRY_RUN=true`
- `PLANNER_GATED_TASK_FAMILIES=knowledge_lookup,runtime_status,reporting`
- `PLANNER_GATED_REQUIRE_DIFF_STATUS=aligned,acceptable_diff`
- `PLANNER_GATED_MAX_DIFF_SEVERITY=warning`

Default selected execution source is always `legacy_policy`.

## Active Mode Boundary

Active mode is implemented, but only for:

- knowledge lookup -> `knowledge`
- runtime status -> `sql`
- report handoff/reporting -> `report`

Final runtime tools are the intersection of shadow authorized tools, legacy runtime tools, hard allowed tools, and authorization-filtered runtime tools.

## Policy And Runtime Tool Impact

`TaskType`, `intent_stack`, and legacy workflow policy are retained.

Disabled and dry-run modes do not change `enabled_nodes` or `runtime_tools`.

Active mode can only narrow or project the read-only nodes above. It cannot enable action/workorder, diagnosis, or extra runtime tools.

## Blocked Scenarios

Planner gate falls back to legacy for:

- unsupported task families
- diagnosis goals
- action/workorder goals
- ambiguous context
- action follow-up context
- `needs_review` or `unsafe_mismatch` planning diff
- critical planning diff
- shadow authorized tool scope violation
- missing or denied auth context
- explicit device switch that still references an old artifact

## Safety Boundary

The gate does not let the planner call tools or choose arbitrary tools. It only applies a narrow projection after policy, authorization, shadow planning, and planning diff evaluation.

Stages and tool invocation code do not read `planner_gate`.
