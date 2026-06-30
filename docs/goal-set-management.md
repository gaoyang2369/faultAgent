# GoalSet Management

GoalSet is the structured intent layer introduced after `ResolvedContext`. It explains what the user wants to accomplish, while preserving the existing restricted single-agent workflow.

## Contracts

`IntentGoal` represents one user goal:

- `goal_id`: stable identifier such as `goal_1_refresh_current_status`.
- `goal_type`: deterministic goal category.
- `status`: `ready`, `blocked`, or `skipped`.
- `depends_on`: other `goal_id` values that must happen first.
- `required_slots` and `missing_slots`: slots needed to pursue the goal.
- `required_evidence`: evidence needed to answer safely.
- `expected_output`: `answer`, `report`, `workorder_decision`, or `clarification`.
- `risk_level`: `read_only`, `requires_confirmation`, or `high_risk`.
- `source`: explicit user request, context inference, or compatibility projection.
- `context_refs`: artifact/report ids safely referenced by this goal.
- `reason`: short deterministic explanation.

`GoalSet` groups goals for one turn:

- `schema_version = "goal_set.v1"`
- `primary_goal_id`
- `goals`
- `execution_order`
- `blocked_goals`
- `intent_stack_projection`
- `goal_summary`

GoalSet is never a tool plan. It is a structured expression of intent and missing evidence.

## Boundary With ResolvedContext

`ResolvedContext` answers: "what prior context can this turn safely use?"

It owns:

- relation to previous turn
- inherited slots
- referenced artifact/report ids
- stale evidence
- missing context
- authorization-scoped inheritance

`GoalSet` answers: "what does the user want to do with the current message and resolved context?"

It owns:

- goal decomposition
- goal dependencies
- blocked goal state
- evidence needs
- compatibility projection to `intent_stack`

GoalSet consumes `ResolvedContext`; it does not perform artifact projection or permission checks itself.

## Boundary With Workflow

Phase 2 keeps these legacy workflow fields:

- `TaskType`
- `intent_stack`
- `plan_mode`
- `evidence_mode`
- `context_resolution`
- `resolved_context`
- `subgoals`

Workflow policy and tool selection still consume `TaskType`, merged `intent_stack`, policy rules, authorization, and resolved context. Goals do not directly enable tools or nodes.

This is intentional: Phase 2 makes intent observable and testable without changing the execution boundary.

## Projection

GoalSet projects to legacy intent names:

| goal_type | intent_stack_projection |
| --- | --- |
| `explain_fault_code` | `explain_alarm_code` |
| `check_runtime_status` | `check_current_status` |
| `refresh_current_status` | `check_current_status` |
| `diagnose_fault` | `fault_diagnosis` |
| `assess_severity` | `severity_assessment` |
| `recommend_resolution` | `resolution_recommendation` |
| `generate_report` | `report_generation` |
| `decide_workorder` | `workorder_decision` |
| `answer_meta_question` | `permission_scope_query` |
| `clarify_missing_context` | no tool intent |

The final route uses:

```python
final_intent_stack = stable_dedupe(goal_set.intent_stack_projection + legacy_intent_candidates)
```

This keeps existing policy/eval/frontend behavior stable while GoalSet matures.

## Dependencies And Ordering

`depends_on` must reference `goal_id`, not `goal_type`.

Good:

```json
{"goal_id": "goal_2_decide_workorder", "depends_on": ["goal_1_refresh_current_status"]}
```

Bad:

```json
{"goal_id": "goal_2_decide_workorder", "depends_on": ["refresh_current_status"]}
```

`execution_order` uses dependency topological sorting. Goals without dependencies use deterministic priority:

`clarify_missing_context`, `refresh_current_status`, `check_runtime_status`, `explain_fault_code`, `diagnose_fault`, `assess_severity`, `recommend_resolution`, `decide_workorder`, `generate_report`, `answer_meta_question`.

`primary_goal_id` selection:

- Prefer explicit output goals: `generate_report`, then `decide_workorder`.
- Otherwise prefer `diagnose_fault`.
- Otherwise choose the first ready goal in execution order.
- A blocked goal should not be primary unless the only viable goal is `clarify_missing_context`.

## Blocked Goals

A blocked goal means the user has a real goal, but the agent cannot safely pursue it until required context, slots, authorization, or evidence are available.

Examples:

- `assess_severity` blocked because "它" refers to multiple candidate devices.
- `decide_workorder` blocked because the current identity cannot inherit the prior artifact.
- `check_runtime_status` blocked because no device is bound.

Blocked goals are for explanation and planning. They must not enable tools by themselves.

## Workorder Boundary

`decide_workorder` means:

- decide whether a workorder draft is reasonable
- produce a draft/confirmation recommendation
- explain missing evidence or freshness limits

It never means:

- workorder has been dispatched
- workorder has been created in an external system
- a device action has been executed
- a reset or repair was performed

For stale evidence, `decide_workorder` must depend on `refresh_current_status` by `goal_id`, or clearly remain blocked/needs-refresh in reason and required evidence.
