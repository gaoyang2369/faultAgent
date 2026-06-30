# Planner Gate Phase 4.3.5 Acceptance

Phase 4.3.5 validates planner gate behavior across disabled, dry-run, and active modes. It does not enter Phase 4.4.

## Acceptance Scope

Validated gate modes:

- `disabled`: all requests select `legacy_policy`
- `dry_run`: eligible read-only requests may be detected, but still select `legacy_policy`
- `active`: only read-only `knowledge_lookup`, `runtime_status`, and `reporting` may select `planner_gated`

Validated blocked scenarios:

- diagnosis
- action/workorder
- `decide_workorder` goal
- `diagnose_fault` goal
- ambiguous context
- action follow-up context
- `needs_review` planning diff
- `unsafe_mismatch` / critical planning diff
- missing or denied auth
- unauthorized inheritance
- stale workorder

## Active Read-Only Boundary

Active mode is currently limited to:

- `knowledge_lookup`: final nodes may project only `knowledge`
- `runtime_status`: final nodes may project only `sql`
- `reporting`: final nodes may project only `report`

The gate never enables diagnosis, workorder, device control, dispatch, reset, parameter write, or confirmation-bypassing actions.

If `permission_check`, `risk_check`, `audit_log`, `output_guardrail`, or `evidence_validation` are enabled by both legacy policy and the shadow plan, active projection preserves them. If the shadow plan drops a legacy safety node, the gate falls back to legacy instead of applying the projection.

## Runtime Tool Intersection

Final runtime tools must be:

```text
shadow authorized tools
INTERSECT legacy runtime_tools
INTERSECT hard allowed tools
INTERSECT authorization-filtered runtime_tools
```

Acceptance tests verify that active mode can narrow tool sets but cannot expand them.

## Evidence From Tests

`tests/test_planner_gate.py` covers:

- default disabled behavior
- dry-run eligible behavior
- active knowledge/runtime/report projections
- diagnosis/action/ambiguous/diff/tool/auth blockers

`tests/test_intent_context_behavior_invariants.py` verifies:

- `stages.py` does not read `planner_gate`
- `runner.py` tool-call section does not read `planner_gate`
- `tools/` does not read `planner_gate`
- workflow policy does not read `planner_gate`

`scripts/planner_gate_acceptance_test.py` validates plan-endpoint behavior for disabled, dry-run, active read-only, and blocked scenarios.

`scripts/planner_gate_observation_report.py` writes aggregate reports to:

- `trash/run/planner_gate_observation_report.json`
- `trash/run/planner_gate_observation_report.md`

Latest observation run:

- total mode-case observations: `126`
- disabled observations: `42`
- dry-run eligible observations: `13`
- active eligible observations: `13`
- selected `planner_gated`: `13`
- selected `legacy_policy`: `113`
- fallback observations: `113`
- enabled node changes in active mode: `13`
- runtime tool changes in active mode: `4`
- runtime tool expansions: `0`
- critical planning diffs: `0`

The active changes are projections onto read-only nodes/tools. No active case expands runtime tools beyond the disabled-mode legacy baseline.

Active enabled-node projection cases:

- `single_status_j1`
- `single_alarm_code_qa`
- `single_report_generation`
- `composite_multi_codes`
- `composite_report_freshness`
- `followup_report_handoff`
- `followup_switch_asset`
- `missing_device_status`
- `missing_action_type`
- `report_without_evidence`
- `engineer_assigned_asset`
- `exception_sql_empty`
- `exception_kb_miss`

Active runtime-tool narrowing cases:

- `single_report_generation`
- `composite_report_freshness`
- `followup_report_handoff`
- `missing_device_status`

Top fallback reasons:

- `unsupported_task_family`: `72`
- `diagnosis_not_migrated`: `48`
- `planner_gate_disabled`: `42`
- `diff_status_not_allowed`: `42`
- `action_or_workorder_not_migrated`: `39`
- `empty_final_runtime_tools`: `39`
- `blocked_context_relation:action_followup`: `18`
- `unauthorized_or_missing_auth_context`: `15`

## Phase 4.4 Readiness

Phase 4.3.5 can support a Phase 4.4 design review, but it does not justify immediate diagnosis/action/workorder active migration. Diagnosis and action/workorder still require stronger evidence, guardrail, and confirmation gates before any execution migration.
