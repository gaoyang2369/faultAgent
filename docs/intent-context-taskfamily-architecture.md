# Intent, Context, And TaskFamily Architecture

This document summarizes the current Phase 1-3 architecture. The runtime is still a restricted single-agent workflow, not an open-ended agent loop.

## Layer Responsibilities

`ResolvedContext` answers: what context can this turn safely reuse?

- It resolves relation to previous turns, inherited slots, referenced artifacts/reports, stale evidence, missing context, and authorization-scoped inheritance.
- It is built from artifact-backed case projection.
- It does not replace task classification or workflow policy.

`GoalSet` answers: what does the user want to accomplish this turn?

- It contains structured `IntentGoal` entries, dependencies, blocked goals, expected output, evidence needs, and compact summaries.
- It projects goals to legacy intent names.
- Goals do not directly enable tools or workflow nodes.

`intent_stack` is the compatibility execution field:

```text
intent_stack = stable_dedupe(goal_set.intent_stack_projection + legacy_intent_candidates)
```

Workflow policy still consumes this merged field.

`TaskType` is the existing fine-grained workflow classification. It remains the primary input to policy selection.

`task_family` is an observational coarse mapping derived from `TaskType`. It is used for debug, trace, eval, and migration planning only. It does not change execution.

`WorkflowPolicy` still decides node and tool availability through:

- `TaskType`
- merged `intent_stack`
- route flags
- policy rules
- authorization and tool gateway checks

## Artifact, EvidenceBundle, And CaseState

`DiagnosisArtifactEnvelope` is the thread-level source of truth for prior results. It may contain request, decision, SQL/knowledge/analysis/report artifacts, workorder suggestion, evidence bundle payload, and optional `case_state_snapshot`.

`CaseState` is a projection from artifacts. It is not a separate authoritative persistence layer. If `case_state_snapshot` is absent or invalid, projection falls back to the raw artifact payload.

`EvidenceBundle` is the per-run evidence ledger used to validate claims and final answers. It is not the long-term context store, but it can be saved inside artifacts and reused for follow-up projection.

## Source Of Truth And Compatibility Fields

Source of truth:

- Current user request and trusted `AuthContext`
- `DiagnosisArtifactEnvelope` payloads
- Runtime outputs from SQL, knowledge, analysis, report, workorder, and evidence validation stages
- `WorkflowPolicy` and authorization decisions for execution

Derived/debug/compatibility fields:

- `resolved_context`
- `goal_set`
- `goals`
- `intent_stack`
- `task_family`
- `context_resolution`
- compact trace summaries

Compatibility fields retained across Phase 1-3:

- `TaskType`
- `intent_stack`
- `context_resolution`
- `evidence_mode`
- `subgoals`

## Execution Boundaries

Goals do not directly enable tools.

`task_family` does not directly enable tools.

Before Phase 4, policy must not read `task_family` as an execution input. It may only appear in debug, plan, complete, trace, artifact, and eval surfaces.

High-risk action and workorder behavior remains guarded by permission checks, risk checks, human confirmation language, output guardrails, and the restricted tool whitelist.
