# Plan And Complete Debug Contract

This document records the compact debug fields exposed by `/chat/plan`, SSE `complete`, and trace metadata after Phase 3.

## `/chat/plan`

Key debug fields:

- `resolved_context`: compact summary of context resolution.
- `goal_set`: compact goal summary.
- `goals`: compact per-goal list.
- `task_family`: coarse observational family.
- `primary_task_type`: exposed through `intent_axes.domain_task` and `workflow_route.primary_task_type`.
- `intent_stack`: exposed through `intent_axes.intent_stack` and `workflow_route.intent_stack`.
- `workflow_route`: compact route snapshot.
- `workflow_policy`, `enabled_nodes`, `planned_tools`: plan-only execution preview.

`/chat/plan` must stay side-effect-free. It must not call SQL, RAG, report tools, final answer generation, or artifact writes.

## SSE `complete`

Key debug fields:

- Top-level `resolved_context`: compact summary.
- Top-level `goal_set`: compact summary.
- Top-level `task_family`: compact family value.
- `decision`: full decision structure for compatibility and offline debugging.
- `workflow_route`: compact route summary when that complete shape already includes a route.
- `trace`: runtime trace events and compact metadata.

Direct lightweight replies keep their compact complete shape and do not force a `workflow_route`.

## Trace Metadata

Trace metadata may include:

- `resolved_context`: compact summary.
- `goal_set`: compact summary.
- `task_family`, `task_family_reason`, `task_family_source`, `task_family_warnings`.
- `primary_task_type`, policy id, event counts, tool call counts, authorization summary.

Trace metadata should remain compact and suitable for debugging/eval.

## Compact Versus Full Structures

Compact summaries:

- Top-level `resolved_context`
- Top-level `goal_set`
- Top-level `task_family`
- `workflow_route.goal_set`
- trace `resolved_context`
- trace `goal_set`

Potentially fuller structures:

- `decision.goal_set`
- `decision.goals`
- saved artifact payloads
- detailed trace events

Do not place long evidence bodies, raw SQL text, raw tool payloads, or report body content into compact summaries.

## Simplified Example

```json
{
  "type": "chat_complete",
  "primary_task_type": "alarm_triage",
  "task_family": "diagnosis",
  "resolved_context": {
    "relation_to_previous": "new_case",
    "inherited_slots": {},
    "stale_evidence": false
  },
  "goal_set": {
    "primary_goal_id": "goal_1_check_runtime_status",
    "goal_types": ["explain_fault_code", "check_runtime_status"],
    "intent_stack_projection": ["explain_alarm_code", "check_current_status"]
  },
  "workflow_route": {
    "primary_task_type": "alarm_triage",
    "task_family": "diagnosis",
    "intent_stack": ["explain_alarm_code", "check_current_status"],
    "goal_set": {
      "goal_types": ["explain_fault_code", "check_runtime_status"]
    },
    "resolved_context": {
      "relation_to_previous": "new_case"
    }
  },
  "decision": {
    "primary_task_type": "alarm_triage",
    "task_family": "diagnosis",
    "intent_stack": ["explain_alarm_code", "check_current_status"],
    "goal_set": {
      "goals": [
        {"goal_id": "goal_1_explain_fault_code", "goal_type": "explain_fault_code"},
        {"goal_id": "goal_2_check_runtime_status", "goal_type": "check_runtime_status"}
      ]
    }
  }
}
```
