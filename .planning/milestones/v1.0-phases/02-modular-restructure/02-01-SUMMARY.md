---
phase: 02-modular-restructure
plan: 01
subsystem: config
tags: [dotenv, env-var, json-serialization, todo-parsing, utility]

# Dependency graph
requires:
  - phase: 01-safety-net
    provides: "22 characterization tests as regression safety net"
provides:
  - "config.py: centralized configuration with 8 env-var-overridable constants"
  - "utils.py: 7 utility functions for JSON serialization and todo parsing"
  - "52 unit tests for config and utils modules"
affects: [02-modular-restructure, 03-tool-extraction]

# Tech tracking
tech-stack:
  added: []
  patterns: ["module-level constants with os.getenv defaults", "importlib.reload for config testing"]

key-files:
  created: [config.py, utils.py, tests/test_config.py, tests/test_utils.py]
  modified: []

key-decisions:
  - "Module-level constants only in config.py (no class, no dataclass, no pydantic-settings per user preference)"
  - "load_dotenv(override=True) called once at config module import time"
  - "Moved langchain_core.messages import from inline (tools.py L399) to module-level in utils.py"

patterns-established:
  - "Config testing: monkeypatch.setenv + importlib.reload(config) for env var override testing"
  - "Utils module: no side effects, no project-specific imports, only stdlib + langchain_core"

requirements-completed: [CONF-01, CONF-02]

# Metrics
duration: 3min
completed: 2026-03-26
---

# Phase 02 Plan 01: Config & Utils Foundation Summary

**Centralized config.py with 8 env-var constants and utils.py with 7 JSON/todo utility functions extracted from tools.py**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-26T07:37:34Z
- **Completed:** 2026-03-26T07:41:06Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- Created config.py with all 8 hardcoded values extracted from app.py, tools.py, knowledge_base.py, and subagent/call_api_tool.py
- Created utils.py with all 7 utility functions (3 public, 4 private) extracted from tools.py lines 389-583
- Added 52 comprehensive unit tests (18 for config, 34 for utils) covering defaults, types, overrides, serialization, and structure constraints
- All 74 tests pass (22 existing + 52 new) with zero regressions

## Task Commits

Each task was committed atomically:

1. **Task 1: Create config.py with all 8 hardcoded values** - `429fee3` (feat)
2. **Task 2: Create utils.py with utility functions extracted from tools.py** - `70f1712` (feat)

## Files Created/Modified
- `config.py` - Centralized configuration: OLLAMA_BASE_URL, EMBEDDING_MODEL, FAISS_PATH, MAX_TOKENS_BEFORE_SUMMARY, MESSAGES_TO_KEEP, RECURSION_LIMIT, DCMA_DB_NAME, FAULT_API_URL
- `utils.py` - Generic utilities: sanitize_for_json, safe_json_dumps, parse_todos_from_tool_output (+ 4 private helpers)
- `tests/test_config.py` - 18 unit tests for config defaults, types, env overrides, and structure
- `tests/test_utils.py` - 34 unit tests for JSON serialization, todo parsing, status normalization, and module structure

## Decisions Made
- Module-level constants only in config.py (no class, no dataclass, no pydantic-settings) per user's explicit deferral of settings class
- load_dotenv(override=True) called at module import time -- matches existing pattern across codebase
- Moved langchain_core.messages import from inline position (tools.py line 399) to module-level import block in utils.py for clarity

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- config.py and utils.py are ready for Plan 02 to rewire imports in app.py, tools.py, knowledge_base.py, and subagent/call_api_tool.py
- Both modules are additive (no existing code modified) so Plan 02 can safely change imports

## Self-Check: PASSED

All files verified present: config.py, utils.py, tests/test_config.py, tests/test_utils.py, 02-01-SUMMARY.md
All commits verified: 429fee3, 70f1712

---
*Phase: 02-modular-restructure*
*Completed: 2026-03-26*
