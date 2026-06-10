# Milestones

## v1.0 AI Agent 后端模块化重组 (Shipped: 2026-03-27)

**Phases completed:** 5 phases, 11 plans, 22 tasks

**Key accomplishments:**

- Removed hardcoded API keys from source files, fixed missing import os bug, and created full pytest infrastructure with session-scoped mocks, FakeToolCallingModel, and SSE helpers
- SSE event sequence and structure characterization tests (6 tests) plus smoke tests (4 tests) verifying app import, endpoint 200 status, and SSE event flow
- 12 characterization tests covering SSE tool event formatting (get_time, sql_inter) and history/todos API response structures with async generator mocking
- Centralized config.py with 8 env-var constants and utils.py with 7 JSON/todo utility functions extracted from tools.py
- Rewired app.py, tools.py, knowledge_base.py, and subagent/call_api_tool.py to import from centralized config.py and utils.py, removing all 8 hardcoded values and extracting utility functions
- 6-file tools/ package with lazy SQL init, globals() sharing for data/viz tools, and 9-tool assembled list
- Subagent migrated to tools/subagent/ with lazy DB singleton, relative imports, and get_tools() deferred loading pattern
- Wired tools/ package into app.py, deleted old tools.py and subagent/, added lazy init test proving TOOL-02
- KB build parameters (chunk_size, chunk_overlap, batch_size) externalized to config.py with environment-variable overrides
- Extracted 134-line SSE streaming generator into streaming.py, slimmed app.py to 256 lines with only lifespan/routes/CORS/static-files, all 76 tests pass

---
