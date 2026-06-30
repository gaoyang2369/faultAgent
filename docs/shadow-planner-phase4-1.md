# Shadow Planner Phase 4.1

Phase 4.1 adds a deterministic shadow planner. It observes the current request, context, goals, task type, intent stack, task family, authorization summary, and legacy workflow output. It does not change execution.

## What It Consumes

`PlanningInput` includes:

- user message
- normalized request payload summary
- trusted auth summary
- `ResolvedContext`
- `GoalSet`
- legacy `primary_task_type`
- legacy `intent_stack`
- `task_family`
- referenced artifact/report ids
- compact evidence references

The shadow planner uses only deterministic rules. It does not call the LLM and does not select tools for execution.

## What It Produces

`PlanningDecision` contains:

- `planner_mode = "shadow"`
- `NodePlan` list
- `EvidencePlan`
- `ToolPlan`
- `OutputPlan`
- `legacy_projection`
- `planner_warnings`
- `planner_summary`

`NodePlan` describes desired node state, source goals, required slots/evidence, and guardrails.

`EvidencePlan` describes required, reusable, stale, or missing evidence plus refresh/disclosure requirements.

`ToolPlan` lists candidate tools and the authorized runtime tools that are already present in the legacy runtime allowlist.

`OutputPlan` describes expected output and report/workorder/final-answer boundaries.

## Boundary With Legacy Workflow Policy

Legacy execution still uses:

- `TaskType`
- `intent_stack`
- route flags
- `WorkflowPolicy`
- authorization and tool gateway checks

`shadow_plan` must not be read by `workflow/policies.py`, `evidence_gap.py`, SQL/RAG/report/workorder stages, or tool invocation logic. It is emitted only for debug, eval, trace, and artifact inspection.

Goals and task family still do not directly enable tools. The shadow planner may suggest candidate tools, but real execution keeps using legacy `runtime_tools`.

## Output Surfaces

Compact `shadow_plan` summary appears in:

- `/chat/plan`
- `PlanSnapshot.intent_axes`
- `PlanSnapshot.workflow_route`
- SSE `complete` top-level payload
- SSE `complete.workflow_route` when present
- `SingleAgentDecision.shadow_plan_summary`
- trace metadata

The full shadow plan may be saved additively inside the `workflow_route` artifact for offline debugging.

Compact summaries intentionally omit SQL source text, long evidence bodies, raw tool payloads, and report body content.

## Safety Rules

Stale workorder follow-up requires:

- `EvidencePlan.refresh_required = true`
- `OutputPlan.required_disclosures` includes `evidence_stale`
- `OutputPlan.workorder_boundary = "only_draft_or_confirmation"`

Direct action, workorder, reset, dispatch, or apply semantics must remain guarded. The shadow planner must never describe such actions as completed.

## Phase 4.2 Use

Phase 4.2 can add a policy diff evaluator that compares:

- legacy enabled nodes vs shadow enabled nodes
- legacy runtime tools vs shadow authorized runtime tools
- legacy evidence mode vs shadow evidence requirements
- output boundaries and disclosure requirements

Only after evals prove safe consistency should any low-risk task family move from shadow-only planning to execution influence.
