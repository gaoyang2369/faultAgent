# Intent And Context Roadmap

This roadmap keeps the restricted single-agent architecture intact while making context and intent more explicit.

## Phase 1: ResolvedContext

Purpose: resolve safe multi-turn context before routing.

Delivered:

- `ContextManager.resolve(...)`
- `ResolvedContext`
- artifact-backed case projection
- auth-scoped inheritance
- stale evidence detection
- compact context debug output in plan, complete, and trace

Phase 1 answers: "what prior context may this turn safely reuse?"

## Phase 2: GoalSet

Purpose: express structured user goals without changing tool selection.

Delivered:

- `IntentGoal`
- `GoalSet`
- deterministic goal construction
- goal dependencies and blocked goals
- projection to legacy `intent_stack`
- compact goal debug output in plan, complete, and trace

Phase 2 answers: "what does the user want to accomplish?"

Important boundary:

- Goals do not directly enable tools.
- Workflow policy still consumes merged `intent_stack`.
- Tool calls still go through existing policy, runtime tools, authorization, and runner limits.

## Phase 3: TaskFamily Compatibility Mapping

Purpose: reduce top-level task fragmentation without changing execution behavior.

Planned:

- Add a compatibility `task_family` mapping beside `TaskType`.
- Map diagnosis-like tasks into a diagnosis family.
- Map reports into a reporting family.
- Map workorder/action requests into an action/workorder family.
- Keep existing `TaskType` as the public compatibility field.

Phase 3 should not rewrite workflow policy or tool selection. It should first expose family-level debug and eval fields only.

## Phase 4: Unified Planner Consumption

Purpose: let planner/policy consume context, goals, and task family coherently.

Planned:

- Use `ResolvedContext` for safe inheritance.
- Use `GoalSet` for goal decomposition and dependencies.
- Use `task_family` for coarse workflow grouping.
- Keep legacy fields as compatibility projections until downstream consumers migrate.

Phase 4 is the point where workflow policy may be redesigned. It should be a separate implementation phase with its own eval gates.

## Compatibility Fields Kept

These fields remain stable across the roadmap:

- `TaskType`
- `intent_stack`
- `context_resolution`
- `evidence_mode`
- `subgoals`

They remain because frontend progress, evals, workflow policy, and older artifacts still depend on them.

## Current Data Flow

Current turn:

1. Understand request into payload.
2. Resolve context with `ContextManager.resolve`.
3. Build route with existing `TaskType` classification.
4. Build `GoalSet` from message, payload, route hints, and resolved context.
5. Merge `goal_set.intent_stack_projection` with legacy intent candidates.
6. Select workflow policy from `TaskType` and merged `intent_stack`.
7. Apply authorization and existing tool limits.

This preserves deterministic execution while making the intent layer easier to inspect and evolve.
