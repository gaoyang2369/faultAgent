# Phase 4.4 Diagnosis And Action Migration Design

This is a design document only. It does not authorize implementation or runtime behavior changes.

## Why Not Direct Migration

Diagnosis, action, and workorder flows carry higher risk than read-only lookup/status/report handoff:

- diagnosis may overstate root cause without complete evidence
- action/workorder flows can imply real-world operational changes
- stale evidence can make current-status or workorder decisions unsafe
- permission, risk, audit, and output boundaries must be preserved
- users may interpret completed-action wording as actual execution

Phase 4.4 must therefore stay gated, observable, and reversible.

## Diagnosis Gates Required

Before diagnosis can move beyond shadow/dry-run, the gate must prove:

- evidence completeness is sufficient for every major claim
- no missing critical evidence remains undisclosed
- stale evidence is refreshed or clearly disclosed
- SQL and RAG evidence align with the generated conclusion
- unsupported high-risk conclusions are blocked
- root cause, symptom, hypothesis, and recommendation are clearly separated
- output guardrail detects unsupported confirmed diagnoses

## Action And Workorder Gates Required

Before action/workorder migration, the gate must require:

- `permission_check`
- `risk_check`
- `audit_log`
- human confirmation
- `output_guardrail`
- stale refresh or stale disclosure
- draft-only workorder boundary
- no dispatch/apply/reset/stop/start/config-write semantics

Workorder output must remain a draft or recommendation until explicit human confirmation is implemented and audited.

## Suggested Phase Split

`4.4.1 diagnosis shadow-to-gated dry_run`

- Add diagnosis-specific gate checks.
- Keep selected execution source as legacy.
- Collect mismatch and evidence completeness statistics.

`4.4.2 diagnosis limited active for read-only explanation`

- Allow only read-only explanation shaping.
- Do not allow workorder/action enablement.
- Keep SQL/RAG/report authorization intersections.

`4.4.3 workorder decision dry_run only`

- Evaluate draft-only workorder decisions without changing execution.
- Require permission, risk, audit, freshness, and guardrail pass.

`4.4.4 workorder draft gated active only after manual confirmation`

- Only consider active draft creation after explicit confirmation design exists.
- Never dispatch automatically.

## Explicitly Forbidden

Phase 4.4 must not implement:

- automatic workorder dispatch
- automatic reset
- automatic stop/start of equipment
- automatic parameter changes
- confirmed diagnosis without evidence
- action completion claims without actual audited execution
- bypassing permission, risk, audit, evidence validation, or output guardrail

## Minimum Acceptance Before Implementation

Before implementing any Phase 4.4 active behavior:

- Phase 4.3.5 observation report must show no tool expansion
- diagnosis dry-run must show no critical evidence gaps
- action/workorder dry-run must show all guardrails preserved
- stale evidence paths must refresh or disclose
- final answer templates must avoid completed-action semantics
