# Phase 4 Unified Planner Design

This is a design draft only. It does not authorize implementation or runtime behavior changes.

## Goal

Phase 4 should let planner and policy gradually consume `ResolvedContext`, `GoalSet`, and `TaskFamily` together instead of replacing `TaskType` and `intent_stack` in one step.

The migration must preserve the restricted single-agent architecture:

- no open-ended agent loop
- no model-selected tools
- no unguarded action/workorder execution
- SQL/RAG/report tools still pass whitelist and authorization checks

## Candidate Contracts

`PlanningInput`

- user message and normalized request payload
- trusted auth context summary
- `ResolvedContext`
- `GoalSet`
- legacy `TaskType`
- legacy `intent_stack`
- `task_family`
- current artifact/evidence references

`PlanningDecision`

- selected planning mode
- ordered `NodePlan` list
- `EvidencePlan`
- `ToolPlan`
- `OutputPlan`
- compatibility projection back to legacy fields
- diff metadata against legacy policy

`NodePlan`

- node name
- desired state: enabled, skipped, blocked, shadow-only
- reason
- required slots/evidence
- guardrails

`EvidencePlan`

- required evidence
- reusable evidence
- stale or missing evidence
- refresh requirements
- disclosure requirements

`ToolPlan`

- candidate tools
- authorized runtime tools
- denied tools with reasons
- whitelist and permission provenance

`OutputPlan`

- expected output type
- required disclosures
- report/workorder boundaries
- final answer guardrails
- compact debug summary

## Migration Principles

Start in shadow mode. The new planner should produce suggested plans without affecting execution.

Compare old policy and new planner for:

- enabled nodes
- skipped nodes
- runtime tools
- evidence requirements
- output boundaries

Only after evals show safe consistency should any node family switch to the new planner.

High-risk action and workorder flows must keep human confirmation, permission checks, risk checks, audit logs, and output guardrails.

SQL, RAG, and report tools must still pass current whitelist, ACL, and tool gateway checks.

## Suggested Phases

Phase 4.1 shadow planner:

- Build planner contracts and deterministic planner output.
- Emit shadow plan in `/chat/plan`, trace metadata, and eval artifacts.
- Do not change execution.

Phase 4.2 policy diff evaluator:

- Compare legacy policy output with shadow planner output.
- Track mismatches in enabled nodes, runtime tools, evidence gaps, and output plans.
- Add eval gates for regressions.

Phase 4.3 low-risk read-only migration:

- Consider switching low-risk tasks first:
  - knowledge lookup
  - runtime status
  - report handoff
- Keep compatibility projections and fallbacks.

Phase 4.4 diagnosis/action/workorder migration:

- Migrate diagnosis only after read-only tasks are stable.
- Migrate action/workorder last.
- Preserve confirmation and guardrail requirements.

## Eval Gate

Before any Phase 4 planner output affects execution, evals must cover:

- multi-turn context inheritance
- composite goals
- stale workorder follow-up
- unauthorized inheritance
- explicit device switch
- no conclusion without evidence
- runtime tools do not exceed authorization
- final answer discloses missing or stale evidence
- direct actions are never represented as already executed
- report generation uses only authorized evidence

## Non-Goals For Phase 4

- Do not delete `TaskType`.
- Do not delete `intent_stack`.
- Do not remove legacy workflow policy until downstream compatibility is proven.
- Do not let goals or task family directly call tools.
- Do not introduce an open agent loop.
