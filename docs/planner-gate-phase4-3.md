# Planner Gate Phase 4.3

Phase 4.3 adds a read-only planner gate. It decides whether a low-risk request may use the shadow planner as a gated execution projection.

The default remains legacy execution. The gate is disabled unless explicitly enabled by configuration.

## Supported Task Families

Only these task families are considered:

- `knowledge_lookup`
- `runtime_status`
- `reporting`

Diagnosis, action/workorder, stale workorder, ambiguous context, missing authorization context, and unauthorized inheritance stay on legacy policy.

## Modes

`disabled`

- Default.
- Builds `planner_gate` for visibility.
- Always selects `legacy_policy`.

`dry_run`

- Enabled with `ENABLE_PLANNER_GATED_EXECUTION=true` and `PLANNER_GATED_DRY_RUN=true`.
- Computes eligibility and final projection.
- Still selects `legacy_policy`.

`active`

- Enabled with `ENABLE_PLANNER_GATED_EXECUTION=true` and `PLANNER_GATED_DRY_RUN=false`.
- May select `planner_gated` only when every safety condition passes.
- Still intersects planner tools with legacy runtime tools, the hard tool whitelist, and authorization-filtered runtime tools.

## Eligibility

The gate requires:

- allowed read-only task family
- no diagnosis, action/workorder, high-risk, or confirmation goals
- no ambiguous or action-follow-up context
- planning diff status in configured allowed statuses
- planning diff severity at or below configured maximum
- no critical planning diff
- shadow authorized tools are a subset of legacy runtime tools
- final runtime tools are the safe intersection
- safety guardrail nodes are not removed
- no unauthorized inheritance or missing auth context

Any failed condition sets `selected_execution_source = legacy_policy` and `fallback_to_legacy = true`.

## Active Projection

Active mode is intentionally narrow:

- `knowledge_lookup`: planner may project only `knowledge`
- `runtime_status`: planner may project only `sql`
- `reporting`: planner may project only `report`

The planner never calls tools. It only projects node/tool eligibility after legacy policy, authorization, whitelist, and planning diff checks have already run.

## Output Surfaces

Compact `planner_gate` appears in:

- `/chat/plan`
- SSE `complete`
- `workflow_route`
- trace metadata
- `SingleAgentDecision.planner_gate_summary`

The `workflow_route` artifact may store the full gate decision additively for offline debugging.
