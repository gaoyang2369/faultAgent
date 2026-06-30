# Context Management

This project uses artifact-backed context rather than free-form chat memory. The single-agent workflow stays restricted: LLM calls can help with understanding, summarization, and diagnosis text, while tool execution remains selected by deterministic planning and policy.

## Core Contracts

`CaseState`

- A thread-local projection of one diagnosis case.
- Built from `DiagnosisArtifactEnvelope` payloads.
- Carries the active device, fault codes, time window, latest artifact/report/evidence ids, diagnosis summary, evidence freshness, and pending actions.
- May contain `projection_warnings` when `case_state_snapshot` was unusable and raw payload fallback was used.

`ResolvedContext`

- The per-turn context decision produced by `ContextManager.resolve`.
- It is consumed by routing, planning, complete payloads, and trace metadata.
- It does not replace `TaskType`, `intent_stack`, or workflow policy.

`PendingAction`

- A pending action inferred from prior artifacts, for example a workorder decision that still requires confirmation.
- It is not an executed action and must not be rendered as dispatched, completed, reset, or applied.

`ContextReference`

- A lightweight contract for references to prior context or explicit objects.
- Phase 1 keeps this available as a context contract; the resolver currently exposes references as labels in `ResolvedContext.references`.

## ArtifactBackedCaseStore

`ArtifactBackedCaseStore` reads recent artifacts from the current `thread_id` and projects them into `CaseState` candidates.

Projection order:

1. Try `payload.case_state_snapshot` when `schema_version == "case_state_snapshot.v1"`.
2. If the snapshot is missing, malformed, version-mismatched, or inconsistent, fall back to raw artifact payload projection.
3. Raw projection reads request fields, decision objects, legacy context resolution, evidence bundle id, report artifact, analysis artifact, operation report payload, and workorder decision.

The store is read-only for context resolution. It does not add a new case-state persistence backend.

## Snapshot Is Not Source Of Truth

`case_state_snapshot` is only an acceleration cache embedded in an artifact payload. It is not authoritative because:

- It can be absent in older artifacts.
- It can be stale after schema evolution.
- It can be malformed or belong to a different thread.
- The full `DiagnosisArtifactEnvelope` still contains the request, decision, evidence, report, and workorder data needed for projection.

When fallback happens, the resolver keeps the request running and adds the fallback reason to `context_resolution_reason`.

## relation_to_previous

- `new_case`: the current message starts a new diagnostic frame or gives explicit context.
- `continuation`: the user refers to prior diagnosis context without asking for a specific handoff action.
- `report_handoff`: the user asks to generate/export a report based on prior results.
- `action_followup`: the user asks whether to create a workorder or take an action based on prior results.
- `refresh_current_status`: the user asks to refresh current/latest status for the prior object.
- `correction`: the user explicitly changes or corrects the previous object, such as switching from J1 to J2.
- `ambiguous`: the user uses a reference like "它" but there are multiple safe candidates.

`relation_to_previous` in `ResolvedContext` is the canonical context relation. Legacy decision fields may map it into older names for compatibility, such as `actionize_previous_result`.

## inherited_slots

Allowed inherited fields:

- `device`
- `fault_codes`
- `time_window`
- `evidence_bundle`
- `report`

The resolver must not inherit slots when the current message explicitly names a different device or when auth checks fail.

## stale_evidence

Evidence is marked stale when artifact/report text contains any of:

- `stale`
- `STALE`
- `已滞后`
- `滞后`
- `非实时`
- `不代表实时状态`

When stale evidence is used for a workorder follow-up:

- `ResolvedContext.stale_evidence` must be `true`.
- `should_refresh_runtime_data` must be `true`, or stale/missing context must clearly explain the refresh need.
- The final answer must disclose the evidence freshness boundary.
- The workorder output should describe a draft or confirmation step, not direct dispatch.

## Permission Rules

Context resolution enforces permission before inheritance:

- Only artifacts in the current `thread_id` are considered.
- Admin can inherit scoped assets.
- Non-admin users must have the prior device inside `auth_context.asset_scope`.
- Report inheritance requires `data.report.read` or `data.report.read_all`.
- Runtime-data semantic inheritance requires non-empty `table_scope`.
- SQL ACL still performs later table/query checks.
- Explicit new device in the current message always wins over previous context.
- Unauthorized resolution must not inherit prior device, fault code, report, report URL, pending action, or artifact id, and should not leak those details through debug fields.

## Boundary With Workflow

Context layer responsibilities:

- Resolve whether the current turn relates to prior artifacts.
- Choose safe inherited slots.
- Identify stale evidence and missing context.
- Enforce auth-scoped inheritance.
- Provide compact debug context.

Workflow/router responsibilities:

- Keep `TaskType` classification.
- Keep `intent_stack` and route flags.
- Decide enabled workflow nodes through policy.
- Select tools deterministically.
- Apply authorization to node/tool availability.

Phase 1 intentionally keeps `TaskType`, `intent_stack`, `plan_mode`, `evidence_mode`, and legacy context fields. `goals` and `task_family` are not introduced here.

## Debug Surfaces

`resolved_context` appears in:

- `/chat/plan` as `PlanSnapshot.resolved_context`.
- SSE `complete` top-level payload.
- SSE `complete.workflow_route.resolved_context`.
- `decision.resolved_context` for compatibility.
- Local and exported trace metadata.

The compact debug summary includes:

- `relation_to_previous`
- `active_case_id`
- `referenced_artifact_id`
- `referenced_report_id`
- `inherited_slots`
- `pending_actions` with type/status and required-evidence count
- `pending_action_count`
- `pending_action_types`
- `stale_evidence`
- `missing_context`
- `context_resolution_reason`
- `candidates_count`
- `source`
- `used_active_asset`
- `used_active_fault_codes`

It intentionally omits long evidence bodies, SQL source text, and report body content.
