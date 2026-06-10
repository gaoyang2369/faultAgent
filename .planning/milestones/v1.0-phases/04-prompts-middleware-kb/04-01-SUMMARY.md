---
phase: 04-prompts-middleware-kb
plan: 01
subsystem: prompts-middleware
tags: [prompts, middleware, refactor, extraction]
dependency_graph:
  requires: [config.py, tools/]
  provides: [prompts/, middleware.py]
  affects: [app.py]
tech_stack:
  added: []
  patterns: [package-extraction, middleware-assembly-function]
key_files:
  created:
    - prompts/__init__.py
    - prompts/system_prompt.py
    - prompts/dynamic_prompt.py
    - middleware.py
  modified:
    - app.py
  deleted:
    - prompt_template.py
decisions:
  - Deleted ~70 lines of commented-out dead code from old prompt_template.py (per user decision)
  - build_middleware accepts summary_model param to keep model creation in app.py lifespan
metrics:
  duration: 4min
  completed: "2026-03-26T13:31:00Z"
  tasks_completed: 2
  tasks_total: 2
  files_created: 4
  files_modified: 1
  files_deleted: 1
  tests_before: 76
  tests_after: 76
---

# Phase 04 Plan 01: Prompts & Middleware Extraction Summary

Extracted prompt definitions (systemprompt, get_identity_system_prompt) and dynamic prompt logic (Context, identity_aware_prompt) from app.py + prompt_template.py into a new prompts/ package, created middleware.py with build_middleware() assembly function, and deleted the old prompt_template.py.

## What Was Done

### Task 1: Create prompts/ package and middleware.py (3bda903)

Created 4 new files:

- **prompts/system_prompt.py**: Contains `systemprompt` (4033-char multi-line string with all workflow tables, examples, formatting) and `get_identity_system_prompt()` function. Only active code -- deleted ~70 lines of commented-out dead code from the old prompt_template.py.
- **prompts/dynamic_prompt.py**: Contains `Context` dataclass (user_identity field) and `identity_aware_prompt` function with `@dynamic_prompt` decorator. Imports from `prompts.system_prompt`.
- **prompts/__init__.py**: Convenience re-exports of `systemprompt`, `get_identity_system_prompt`, `Context`, `identity_aware_prompt`.
- **middleware.py**: Contains `build_middleware(summary_model)` that returns `[TodoListMiddleware(), identity_aware_prompt, SummarizationMiddleware(...)]`. Reads `MAX_TOKENS_BEFORE_SUMMARY` and `MESSAGES_TO_KEEP` from config.py.

### Task 2: Rewire app.py and delete prompt_template.py (2bf8d32)

- Replaced 4 import lines (`langchain.agents.middleware`, `config` with 3 values, `prompt_template`, `dataclasses`) with 3 new imports (`config` with only `RECURSION_LIMIT`, `prompts.dynamic_prompt`, `middleware`)
- Removed 18-line Context + identity_aware_prompt definition block from app.py
- Replaced 11-line inline middleware assembly (SummarizationMiddleware + middleware list) with single `middleware_list = build_middleware(summary_model)` call
- Deleted `prompt_template.py` entirely
- Net reduction: 249 lines removed from app.py + prompt_template.py

## Verification Results

1. `from prompts import systemprompt, Context, identity_aware_prompt` -- OK
2. `from middleware import build_middleware` -- OK (returns function)
3. `from app import app` -- OK (imports resolve correctly)
4. `prompt_template.py` -- deleted
5. `pytest tests/ -x -q` -- 76 passed
6. `grep -c "from prompt_template" app.py` -- 0
7. `grep -c "class Context" app.py` -- 0

## Deviations from Plan

None -- plan executed exactly as written.

## Decisions Made

1. **Dead code deletion**: Removed ~70 lines of commented-out identity prompt code from lines 10-80 of old prompt_template.py (per user decision: "注释掉的死代码直接删除，只保留活跃代码")
2. **build_middleware parameter**: `summary_model` passed as argument rather than imported, keeping model creation responsibility in app.py lifespan

## Self-Check: PASSED

- FOUND: prompts/__init__.py
- FOUND: prompts/system_prompt.py
- FOUND: prompts/dynamic_prompt.py
- FOUND: middleware.py
- CONFIRMED DELETED: prompt_template.py
- FOUND: commit 3bda903
- FOUND: commit 2bf8d32
