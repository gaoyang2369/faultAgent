# Policy Diff Phase 4.2

Phase 4.2 adds a deterministic Policy Diff Evaluator for comparing legacy workflow policy output with the Phase 4.1 shadow planner. It is observation-only and does not change execution.

## What It Compares

`planning_diff` compares:

- legacy `enabled_nodes` and skipped nodes against shadow desired node states
- legacy `runtime_tools` against shadow candidate and authorized runtime tools
- legacy evidence mode, refresh requirements, and policy evidence requirements against the shadow evidence plan
- requested output and safety boundaries against the shadow output plan
- safety guardrails such as permission checks, risk checks, audit logs, output guardrails, stale evidence disclosure, and workorder/action boundaries

The evaluator is deterministic. It does not call the LLM, does not select tools, and does not modify `decision.flags`, route flags, workflow policy metadata, `enabled_nodes`, or `runtime_tools`.

## Status And Severity

Severity order is:

```text
none < info < warning < error < critical
```

Overall status is:

- `aligned`: no meaningful diff, or only `exact_match/none`
- `acceptable_diff`: only `info`, or allowlisted non-safety warnings
- `needs_review`: warning or error exists, with no critical
- `unsafe_mismatch`: any critical exists

Safety-related warnings default to `needs_review`, not `acceptable_diff`.

## Critical Conditions

Critical diff examples:

- shadow authorized runtime tools exceed legacy `runtime_tools`
- shadow skips legacy safety guardrail nodes such as permission, risk, audit, evidence validation, or output guardrail
- stale workorder/action flow lacks refresh or stale-evidence disclosure
- workorder/action output boundary implies executed, dispatched, applied, reset, or completed action semantics
- unauthorized artifact/report/fault-code reference is detected

## Output Surfaces

`/chat/plan`, SSE `complete`, and trace metadata expose only compact `planning_diff` summaries:

- `overall_status`
- `severity`
- diff counts
- critical/warning counts
- short summary
- compact `diff_types`
- conservative `migration_readiness`

Full diff objects are not placed in plan, complete, or trace payloads.

## Migration Readiness

Phase 4.2 never marks execution migration as safe. `migration_readiness.safe_to_migrate` is always `false`.

The readiness object is only an observation hint:

```json
{
  "read_only_candidate": true,
  "safe_to_migrate": false,
  "reason": "shadow evaluation only; no execution migration in Phase 4.2"
}
```

Any real execution migration requires a separate Phase 4.3 approval.
