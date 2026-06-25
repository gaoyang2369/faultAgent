# Agent Workflow Regression Evals

This directory contains deterministic and trace-level regression checks for the fault diagnosis agent workflow.

## Running Evals

PR plan eval:

```bash
python tests/evals/run_plan_eval.py
```

Trace core eval with the in-process mocked runtime:

```bash
python tests/evals/run_trace_eval.py --subset core
```

Release-style full trace eval:

```bash
python tests/evals/run_trace_eval.py --all --with-llm-judge
```

Both runners also support a remote service:

```bash
python tests/evals/run_plan_eval.py --base-url http://localhost:8000
python tests/evals/run_trace_eval.py --base-url http://localhost:8000 --subset core
```

Plan eval writes `tests/evals/results/plan_eval_summary.json`. Trace eval writes `tests/evals/results/trace_eval_summary.json`.

## Case Schema

Cases live in `agent_workflow_cases.yaml`.

- `id`: Stable case identifier. Keep it unique.
- `name`: Human-readable case name.
- `tier`: `smoke`, `core`, or `extended`.
- `eval_modes`: Any of `plan`, `trace_mock`, `trace_real`.
- `role`: Expected user role for the case.
- `asset_scope`: Explicit asset scope for permission assertions.
- `identity_fixture`: Auth fixture under `fixtures/auth/`.
- `artifact_fixture`: Previous artifact fixture under `fixtures/artifacts/`.
- `sql_fixture`: SQL fixture name reserved for trace mock/real setup.
- `kb_fixture`: KB fixture name reserved for trace mock/real setup.
- `precondition`: Short setup note.
- `turns`: User turns; the last turn is sent to `/chat/plan` or `/chat/stream`.
- `expected.context`: Context binding assertions, such as active artifact reuse.
- `expected.intent`: Domain task, continuation type, object binding, and intent stack assertions.
- `expected.workflow`: Policy, enabled/skipped nodes, `must_enable`, `must_skip`, missing slots, and evidence gaps.
- `expected.tools`: Planned tools and forbidden tools.
- `expected.answer`: Answer contract assertions, especially `must_not_contain` for safety cases.
- `expected.metrics`: Case-specific metric expectations or notes.

Each case must include at least one positive assertion and one negative assertion. Context follow-up cases must define `forbidden` tools or `must_skip` nodes. Safety cases must define `expected.answer.must_not_contain`. Permission cases must define `role` and `asset_scope`.

## Fixtures

Fixture directories are first-class test inputs:

- `fixtures/artifacts/`: Serialized `DiagnosisArtifactEnvelope` objects used to seed previous evidence.
- `fixtures/auth/`: Trusted server-side identity fixtures. Do not rely on frontend `role` or `user_identity` parameters.
- `fixtures/sql_results/`: SQL result fixtures for mocked trace scenarios.
- `fixtures/kb_results/`: Knowledge-base fixtures for mocked trace scenarios.

To add a fixture, create a JSON file in the relevant directory and reference it from the YAML case. Keep fixtures small and focused on the assertion being tested.

## Failure Triage

Start with the JSON summary file. It includes aggregate metrics, failed case ids, and failure reasons.

For plan failures, compare the case expectations against `/chat/plan` fields:

- `resolved_context`
- `intent_axes`
- `workflow_policy`
- `enabled_nodes` / `skipped_nodes`
- `planned_tools` / `forbidden_tools`
- `missing_slots`
- `evidence_gaps`

For trace failures, inspect `complete.trace`, `tool_start` events, and artifacts in the SSE `chat_complete` payload. The plan-stream consistency checks compare `/chat/plan` with `/chat/stream` for the same case and fail when forbidden tools run, `must_skip` nodes execute, or `must_enable` nodes are neither executed nor explained by a skip reason.

## CI Strategy

PR:

```bash
python tests/evals/run_plan_eval.py
```

Nightly:

```bash
python tests/evals/run_trace_eval.py --subset core
```

Release:

```bash
python tests/evals/run_trace_eval.py --all --with-llm-judge
```

Hard gates:

- Plan eval must pass 100%.
- Core plan-stream consistency must pass 100%.
- `contradiction_rate` must be `0`.
- Dangerous action forbidden phrase count must be `0`.
- Previous-artifact-sufficient skip unnecessary tool call rate must be `0`.
