# Goal Phase 2 Change Audit

Phase 2 adds structured `IntentGoal` / `GoalSet` while keeping the existing workflow policy and tool selection path intact.

## File Impact

| File | Why changed | Outputs goals | Still uses legacy intent_stack | SSE complete | /chat/plan | Policy/tool impact |
| --- | --- | --- | --- | --- | --- | --- |
| `single_agent/workflow/contracts.py` | Adds `IntentGoal`, `GoalSet`, and route goal fields. | Yes | Yes | Indirect | Indirect | No policy logic change |
| `single_agent/workflow/goals.py` | Deterministic GoalSet builder and compact summary. | Yes | Projects compatibility intents | Indirect | Indirect | Does not select tools |
| `single_agent/workflow/router.py` | Builds GoalSet and merges projection with legacy intents. | Yes | Yes, merged final field | Indirect | Indirect | Keeps existing classification/policy inputs |
| `single_agent/workflow/__init__.py` | Exports goal contracts and helpers. | Yes | No | No | No | No |
| `single_agent/contracts.py` | Adds `goals` and `goal_set` to `SingleAgentDecision`. | Yes | Yes | Yes through decision dump | Yes | No |
| `single_agent/intent.py` | Carries route goals into decisions and syncs debug projection after legacy adjustments. | Yes | Yes | Indirect | Indirect | No new tool enabling |
| `single_agent/planner.py` | Emits compact `goal_set`, compact `goals`, and `intent_stack_projection`. | Yes | Yes | No | Yes | No |
| `single_agent/output/payloads.py` | Emits compact top-level and workflow_route `goal_set`. | Yes | Yes | Yes | No | No |
| `single_agent/runner.py` | Adds compact goal summary to trace metadata. | Yes | No | No | No | No |
| `single_agent/flow.py` | Saves full goal data in workflow_route artifact. | Yes | Yes | Indirect | No | No |
| `tests/test_goal_set.py` | Goal rules and boundary regression tests. | Test | Test | Test | Test | Test |
| `scripts/context_acceptance_test.py` | Adds GoalSet assertions to context acceptance. | Test | Test | No | Yes | Test |
| `tests/evals/evaluators.py` | Adds goal assertions for plan eval. | Test | Test | No | Yes | Test |
| `tests/evals/agent_workflow_cases.yaml` | Adds representative goal expectations. | Test | Test | No | Yes | Test |

## Modules That Output Goals

- `SingleAgentDecision.goal_set` keeps the full GoalSet.
- `SingleAgentDecision.goals` keeps full goals.
- `/chat/plan` emits compact `goal_set`, compact `goals`, and `intent_stack_projection`.
- SSE complete emits compact top-level `goal_set`.
- SSE `workflow_route.goal_set` emits compact goal summary.
- Trace metadata emits compact `goal_set`.
- Workflow route artifact stores full `goal_set` and `goals` for offline debugging.

## Modules Still Using Legacy intent_stack

- `workflow/policies.py` uses `TaskRoute.intent_stack` for node and tool policy.
- `workflow/evidence_gap.py` uses `intent_stack` for evidence-gap decisions.
- `stages.py` uses `decision.intent_stack` in user-facing safety/summary prefixes.
- Existing evals and frontend progress remain compatible with `intent_stack`.

`TaskRoute.intent_stack` is now:

```python
stable_dedupe(goal_set.intent_stack_projection + legacy_intent_candidates)
```

It is not a raw GoalSet replacement.

## Runtime Contract Impact

- SSE complete: additive compact `goal_set`.
- `/chat/plan`: additive compact `goal_set`, compact `goals`, and `intent_stack_projection`.
- Workflow policy: no behavioral change intended.
- Tool calls: no new tool selection path; still policy/runtime-tools/authorization gated.
- Artifact schema: workflow_route artifact includes full `goal_set` and `goals`; diagnosis artifact backend schema is unchanged.
- Permission strategy: unchanged; goals consume `ResolvedContext` but do not perform permission inheritance.

## Safety Boundaries

- `decide_workorder` only means decision/draft/confirmation.
- It must not mean dispatched, executed, reset, or externally created.
- Stale evidence requires `refresh_current_status` dependency by `goal_id`, or a blocked/needs-refresh reason.
- Goals can explain missing evidence but cannot bypass workflow policy or authorization.
