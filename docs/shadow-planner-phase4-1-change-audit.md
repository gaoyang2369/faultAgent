# Shadow Planner Phase 4.1 Change Audit

Phase 4.1 adds deterministic shadow planning for observation and future diff evaluation. It does not change main-chain execution.

## New And Modified Files

| File | Change |
| --- | --- |
| `fault_diagnosis/single_agent/planning/` | New shadow planner package with contracts, deterministic builder, and compact summaries. |
| `fault_diagnosis/single_agent/contracts.py` | Adds `SingleAgentDecision.shadow_plan_summary`. |
| `fault_diagnosis/single_agent/planner.py` | Adds compact `shadow_plan` to `/chat/plan`, intent axes, and workflow route. |
| `fault_diagnosis/single_agent/output/payloads.py` | Adds compact `shadow_plan` to complete payloads and existing workflow route payloads. |
| `fault_diagnosis/single_agent/flow.py` | Builds shadow plans after legacy decision/authorization and saves full plan in workflow-route artifact. |
| `fault_diagnosis/single_agent/runner.py` | Adds compact `shadow_plan` to trace metadata. |
| `tests/test_shadow_planner.py` | Unit coverage for deterministic planner rules and tool boundaries. |
| `tests/test_intent_context_behavior_invariants.py` | Adds behavior-invariant and no-execution-consumption assertions. |
| `tests/evals/evaluators.py` | Supports `expected.shadow_plan` assertions. |
| `tests/evals/agent_workflow_cases.yaml` | Adds representative shadow-plan eval checks. |
| `scripts/shadow_planner_acceptance_test.py` | Plan-based Phase 4.1 shadow planner acceptance. |
| `docs/shadow-planner-phase4-1.md` | Operational design and boundary documentation. |

## Main Chain Impact

The main execution chain is unchanged. The shadow planner is built after the legacy route, workflow policy, authorization, enabled nodes, and runtime tools are already available.

## `/chat/plan` Impact

`/chat/plan` includes a compact `shadow_plan` summary. It remains side-effect-free and does not call tools, write artifacts, or generate final answers.

## SSE Complete Impact

SSE `complete` includes compact top-level `shadow_plan`. Existing `workflow_route` payloads also include the same compact summary. Direct lightweight replies keep their existing compact shape and only add the summary.

## Trace Impact

Trace metadata includes compact `shadow_plan`:

- `planner_mode`
- `enabled_node_names`
- `blocked_node_names`
- `refresh_required`
- `candidate_tools`
- `authorized_runtime_tools`
- `expected_output`
- warning counts and compact warnings

## Artifact Impact

The `workflow_route` artifact stores full shadow plan output additively. Existing artifact fields are retained.

## Policy And Runtime Tool Impact

Workflow policy is unchanged.

Runtime tools are unchanged.

`workflow/policies.py`, `workflow/evidence_gap.py`, stages, and tool code do not consume `shadow_plan` for execution decisions.

## Security Impact

The shadow planner cannot authorize tools. Authorized runtime tools in `ToolPlan` are only the subset already present in legacy runtime tools and the hard whitelist.

Action/workorder flows retain permission checks, risk checks, audit logging, output guardrails, and human-confirmation boundaries.
