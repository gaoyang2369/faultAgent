# TaskFamily Management

`TaskFamily` is a coarse compatibility label for observing broad request classes while the existing workflow remains based on `TaskType` and `intent_stack`.

## Public Families

The public enum is intentionally small and stable:

- `knowledge_lookup`
- `runtime_status`
- `diagnosis`
- `reporting`
- `action_or_workorder`
- `meta`

Unknown or unsupported task types must not create a new public family. They fall back to an existing public value, normally `meta`, with `task_family_source = "unknown_fallback"` and a warning such as `unknown_task_type`.

## Relationship To TaskType

`TaskType` remains the fine-grained compatibility and policy input. `TaskFamily` is derived from it after routing has produced the legacy task type.

| TaskType | TaskFamily |
| --- | --- |
| `knowledge_qa` | `knowledge_lookup` |
| `status_query` | `runtime_status` |
| `alarm_triage` | `diagnosis` |
| `fault_diagnosis` | `diagnosis` |
| `root_cause_analysis` | `diagnosis` |
| `health_assessment` | `diagnosis` |
| `report_generation` | `reporting` |
| `action_request` | `action_or_workorder` |
| `permission_scope_query` | `meta` |
| `direct_response`, `greeting`, `thanks`, `capability` | `meta` |

Phase 3 does not rename, delete, or replace existing `TaskType` values.

## Relationship To GoalSet

`GoalSet` expresses user goals. `TaskFamily` may inspect goals only as a fallback/debug hint when the task type is unknown or when recording a mismatch warning.

Goals do not select tools. `TaskFamily` also does not select tools.

## WorkflowPolicy Boundary

Workflow policy, node enablement, and runtime tool selection continue to consume:

- `primary_task_type`
- `intent_stack`
- existing route flags
- authorization state
- existing resolved-context policy paths

`TaskFamily` is not read by `workflow/policies.py`, evidence-gap planning, SQL/RAG/report/workorder stages, or the tool allowlist. Phase 3 is an observability and eval compatibility step only.

## Mismatch Warnings

`task_family_goal_mismatch` means the mapped `TaskType` family disagreed with a strong output or goal hint, for example a status task carrying an explicit report output hint.

The warning is debug-only:

- It may appear in `task_family_warnings`, `task_family_reason`, trace metadata, and workflow-route artifacts.
- It must not be written into policy `flags`.
- It must not change `TaskType`, `intent_stack`, enabled nodes, runtime tools, or execution stages.

## Phase 4 Boundary

Planner or policy consumption of `task_family` is deferred to Phase 4. Any future use of `task_family` to influence workflow behavior must be implemented as a separate migration with its own eval gates.
