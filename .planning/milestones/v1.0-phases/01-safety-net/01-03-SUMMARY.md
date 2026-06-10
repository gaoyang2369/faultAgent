---
phase: 01-safety-net
plan: 03
subsystem: testing
tags: [pytest, mock-agent, sse-events, tool-characterization, history-api, async-generator]

# Dependency graph
requires:
  - "01-01: test infrastructure (conftest.py, helpers.py, FakeToolCallingModel)"
provides:
  - "SAFE-02 tool invocation characterization tests for get_time and sql_inter"
  - "SAFE-03 history API characterization tests for /ai/history and /api/todos"
  - "LangChain 1.0 compat shims in conftest for testing in langchain 0.3.x environments"
affects: [all-future-refactoring, 02-core-extraction]

# Tech tracking
tech-stack:
  added: []
  patterns: [async-generator-mock-for-alist, make-tool-call-stream-helper, sse-event-type-filtering]

key-files:
  created:
    - tests/test_tool_calls.py
    - tests/test_history_api.py
  modified:
    - tests/conftest.py

key-decisions:
  - "Mock astream_events with async generators yielding on_tool_start/on_tool_end events rather than testing tools directly"
  - "Use async generator pattern for checkpointer.alist mock since async for requires an async iterable"
  - "Summary statistics in /api/todos are computed AFTER status filtering -- tests capture this behavior"
  - "Added LangChain 1.0 compat shims to conftest.py for cross-environment testing"

patterns-established:
  - "Pattern: make_tool_call_stream() async generator for constructing standardized tool event sequences"
  - "Pattern: async def mock_alist() generator assigned to checkpointer.alist for async-for-compatible mocking"
  - "Pattern: _extract_events_by_type() for filtering parsed SSE events by type field"

requirements-completed: [SAFE-02, SAFE-03]

# Metrics
duration: 11min
completed: 2026-03-26
---

# Phase 01 Plan 03: Tool Call and History API Characterization Tests Summary

**12 characterization tests covering SSE tool event formatting (get_time, sql_inter) and history/todos API response structures with async generator mocking**

## Performance

- **Duration:** 11 min
- **Started:** 2026-03-26T05:43:55Z
- **Completed:** 2026-03-26T05:55:20Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- Created 4 tool call characterization tests verifying SSE event structure for get_time and sql_inter tool invocations (SAFE-02)
- Created 8 history API characterization tests covering /ai/history/{type}, /ai/history/{type}/{chat_id}, and /api/todos/{thread_id} endpoints (SAFE-03)
- Enhanced conftest.py with LangChain 1.0 compat shims (sys.modules injection) and async pool mock fix
- Full test suite (12 tests) passing green

## Task Commits

Each task was committed atomically:

1. **Task 1: Create tool call characterization tests (SAFE-02)** - `6bfd520` (test)
2. **Task 2: Create history API characterization tests (SAFE-03)** - `a3fe730` (test)

## Files Created/Modified
- `tests/test_tool_calls.py` - 4 tests: get_time events, sql_inter events, required fields validation, multi-tool sequence ordering
- `tests/test_history_api.py` - 8 tests: history list, empty list, deduplication, chat messages, missing thread, todos structure, status filter, empty thread
- `tests/conftest.py` - Added _ensure_langchain_1x_compat() for sys.modules injection and AsyncMock for pool.close()

## Decisions Made
- Tested SSE event formatting pipeline (not tools directly) by mocking agent.astream_events with async generators that yield on_tool_start/on_tool_end events
- Used async generator pattern for checkpointer.alist mock since Python's `async for` requires a real async iterable (MagicMock won't work)
- Captured the behavior that /api/todos summary statistics are computed AFTER status filtering (summary.total reflects filtered count, not full count)
- Added LangChain 1.0 module compat shims to sys.modules to support testing in environments with langchain 0.3.x installed

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] LangChain 1.0 imports fail in langchain 0.3.x environment**
- **Found during:** Task 1 (tool call tests)
- **Issue:** `from langchain.agents import create_agent` and `from langchain.agents.middleware import ...` fail because installed langchain 0.3.x doesn't have these 1.0 APIs
- **Fix:** Added `_ensure_langchain_1x_compat()` to conftest.py pytest_configure hook that injects mock modules into sys.modules for langchain.agents.middleware and langgraph.checkpoint.postgres paths
- **Files modified:** tests/conftest.py
- **Verification:** All tests import and run successfully
- **Committed in:** 6bfd520 (Task 1 commit)

**2. [Rule 1 - Bug] AsyncConnectionPool mock missing async close() method**
- **Found during:** Task 1 (tool call tests)
- **Issue:** TestClient teardown triggers `await app.state.pool.close()` in lifespan finally block, but MagicMock().close() returns a non-awaitable MagicMock, causing TypeError
- **Fix:** Added `mock_pool.close = AsyncMock(return_value=None)` to test_client fixture
- **Files modified:** tests/conftest.py
- **Verification:** Tests pass without teardown errors
- **Committed in:** 6bfd520 (Task 1 commit)

---

**Total deviations:** 2 auto-fixed (1 blocking, 1 bug)
**Impact on plan:** Both fixes were necessary to make the test infrastructure work in the current environment. No scope creep.

## Issues Encountered
- Multiple pip install rounds needed (matplotlib, seaborn, pandas, langchain-openai, langchain-ollama, langgraph, uvicorn, psycopg-pool) since worktree environment lacked project dependencies; resolved by incremental installation
- Worktree missing Plan 01 commits -- cherry-picked 9c25a05 and 1bf1d69 to bring in test infrastructure

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Phase 01 safety net complete: 12 characterization tests covering SSE streaming, tool events, and API endpoints
- Tests protect the tool and API contract during refactoring in Phase 02+
- conftest.py patterns established for future test files

## Self-Check: PASSED

All created files verified present. All commit hashes verified in git log.

---
*Phase: 01-safety-net*
*Completed: 2026-03-26*
