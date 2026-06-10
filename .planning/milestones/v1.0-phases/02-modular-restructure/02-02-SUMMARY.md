---
phase: 02-modular-restructure
plan: 02
subsystem: config
tags: [config, utils, imports, refactor, centralization]

# Dependency graph
requires:
  - phase: 02-modular-restructure/01
    provides: config.py with 8 constants and utils.py with 7 utility functions
  - phase: 01-safety-net
    provides: 22 characterization tests as safety net for refactoring
provides:
  - All 4 existing files rewired to import from config.py and utils.py
  - Utility functions removed from tools.py (single source of truth in utils.py)
  - Zero hardcoded config values in app.py, tools.py, knowledge_base.py, subagent/call_api_tool.py
affects: [03-app-decomposition, 04-tool-modularization, 05-knowledge-base-modularization]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Config import pattern: from config import CONSTANT_NAME"
    - "Utils import pattern: from utils import function_name"

key-files:
  created: []
  modified:
    - app.py
    - tools.py
    - knowledge_base.py
    - subagent/call_api_tool.py

key-decisions:
  - "Kept load_dotenv(override=True) in app.py and tools.py since they still read non-config env vars (DB secrets, model name)"
  - "Removed re, ast, typing imports from tools.py after utility function extraction"
  - "Used db_save_path=None with FAISS_PATH fallback in create_knowledge_base for backward compatibility"

patterns-established:
  - "Config centralization: all tunable values come from config.py, not hardcoded"
  - "Utility single source: generic functions live in utils.py, not duplicated across modules"

requirements-completed: [CONF-03]

# Metrics
duration: 4min
completed: 2026-03-26
---

# Phase 02 Plan 02: Import Rewiring Summary

**Rewired app.py, tools.py, knowledge_base.py, and subagent/call_api_tool.py to import from centralized config.py and utils.py, removing all 8 hardcoded values and extracting utility functions**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-26T07:44:35Z
- **Completed:** 2026-03-26T07:48:50Z
- **Tasks:** 3
- **Files modified:** 4

## Accomplishments
- app.py imports utility functions from utils.py and config constants (MAX_TOKENS_BEFORE_SUMMARY, MESSAGES_TO_KEEP, RECURSION_LIMIT) from config.py
- knowledge_base.py reads Ollama URL, embedding model, and FAISS path from config.py instead of hardcoded strings
- tools.py uses DCMA_DB_NAME from config.py; all utility functions (sanitize_for_json, safe_json_dumps, parse_todos family) removed
- subagent/call_api_tool.py uses FAULT_API_URL from config.py instead of hardcoded URL
- Full test suite (74 tests) passes with zero regressions

## Task Commits

Each task was committed atomically:

1. **Task 1: Update app.py and knowledge_base.py imports** - `a66e5ae` (refactor)
2. **Task 2: Update tools.py and subagent/call_api_tool.py, remove utility functions** - `bdfabd6` (refactor)
3. **Task 3: Final validation** - no commit (validation-only, no file changes)

## Files Created/Modified
- `app.py` - Updated imports: tools -> utils for utility functions, added config import for 3 constants, replaced hardcoded values
- `tools.py` - Added config import for DCMA_DB_NAME, removed 195 lines of utility functions, cleaned unused imports
- `knowledge_base.py` - Added config import for OLLAMA_BASE_URL/EMBEDDING_MODEL/FAISS_PATH, replaced hardcoded strings
- `subagent/call_api_tool.py` - Added config import for FAULT_API_URL, replaced hardcoded API URL

## Decisions Made
- Kept `load_dotenv(override=True)` in app.py and tools.py because they still read env vars not yet centralized in config.py (DB secrets like POSTGRES_*, MYSQL_PW, MODEL_NAME are Phase 3+ scope)
- Used `db_save_path=None` default with `FAISS_PATH` fallback in `create_knowledge_base()` to preserve backward compatibility for direct callers like `rebuild_knowledge_base()`
- Removed `re`, `ast`, and `typing` imports from tools.py since they were only used by the now-removed utility functions
- Removed the duplicate `load_dotenv(override=True)` call on tools.py line 31 (kept the one on line 22)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Phase 02 (Config & Utils extraction) is now complete with both plans done
- All 8 hardcoded values centralized in config.py
- All utility functions in utils.py (single source of truth)
- Ready for Phase 03 (app.py decomposition) which will split app.py into agent creation, SSE streaming, and route modules

---
*Phase: 02-modular-restructure*
*Completed: 2026-03-26*
