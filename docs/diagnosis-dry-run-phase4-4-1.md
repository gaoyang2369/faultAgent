# Phase 4.4.1 Diagnosis Planner-Gated Dry Run

Phase 4.4.1 only expands planner-gate observation for `diagnosis` task families. It does not migrate diagnosis execution, action execution, or workorder execution.

## Why Dry Run First

Diagnosis tasks can produce operational conclusions from SQL, knowledge, analysis, evidence bundles, and guardrails. A planner cannot safely take over this path until the project can prove that it preserves evidence requirements, stale-data disclosure, authorization boundaries, and output guardrails.

This phase therefore keeps the legacy workflow as the execution source while adding a compact readiness contract for later evaluation.

## Configuration

New controls:

- `PLANNER_GATE_DIAGNOSIS_DRY_RUN=true`
- `PLANNER_GATE_ENABLE_DIAGNOSIS_ACTIVE=false`

When diagnosis dry-run is enabled, diagnosis tasks enter planner-gate observation with `mode=dry_run`. `selected_execution_source` remains `legacy_policy`.

Even if `ENABLE_PLANNER_GATED_EXECUTION=true` and `PLANNER_GATED_DRY_RUN=false`, diagnosis still cannot select `planner_gated` in this phase.

## Diagnosis Readiness

`diagnosis_readiness` is emitted as a compact summary in `/chat/plan`, SSE complete payloads, `workflow_route`, and trace metadata.

Full contract fields:

- `schema_version`: `diagnosis_readiness.v1`
- `ready_for_active`: always `false`
- `evidence_complete`
- `has_runtime_status`
- `has_manual_reference`
- `has_alarm_or_fault_context`
- `stale_evidence_disclosed`
- `missing_critical_evidence`
- `blocked_reasons`
- `diagnosis_mode`
- `recommended_next_phase`

Compact output fields:

- `diagnosis_mode`
- `evidence_complete`
- `ready_for_active`
- `missing_critical_evidence_count`
- `blocked_reason_count`
- `recommended_next_phase`

## Evidence Completeness

For dry-run readiness, `evidence_complete=true` means the plan has enough signals to be a future migration candidate, not that the runtime has already executed and proven every claim.

The planner checks for:

- Runtime status evidence or reusable non-stale evidence.
- Manual, knowledge, or fault-code reference when required.
- Alarm or fault context for alarm triage, fault diagnosis, and RCA.
- Analysis basis.
- Missing or stale evidence disclosure.
- No critical planning diff.
- Shadow tools staying within legacy runtime tools.
- No unauthorized inherited artifact.

## Blocked Scenarios

Diagnosis readiness stays blocked for:

- Missing runtime status for current-fault questions.
- Stale evidence without disclosure.
- Missing device context.
- Ambiguous context.
- Unauthorized inherited artifact.
- Unsafe or critical planning diff.
- Shadow tools exceeding legacy runtime tools.
- Skipping required evidence or output safety checks.
- Conclusions that would be treated as confirmed without evidence.

RCA and health assessment stay conservative. Their default recommendation is `more_eval` or `keep_legacy`, not `candidate_for_limited_active`.

## Active Boundary

`ready_for_active` is always `false` in this phase. `diagnosis_dry_run_only` and `diagnosis_active_not_enabled` remain planner-gate blockers for diagnosis tasks.

Action and workorder paths remain blocked. This phase does not create, dispatch, close, execute, or modify workorders, and it does not perform device reset, stop/start, or parameter writes.

## Next Phase Conditions

Phase 4.4.2 should be considered only after enough dry-run observations show:

- Stable `candidate_for_limited_active` behavior for low-risk alarm triage or fault diagnosis cases.
- No runtime tool expansion.
- No enabled-node expansion.
- No critical planning diffs.
- Reliable stale and missing evidence disclosure.
- Strong evidence-validation and output-guardrail coverage.
