# Testing Patterns

**Analysis Date:** 2026-03-26

## Test Framework

**Runner:**
- None configured. No test framework is installed or configured for either the Python backend or the Vue/TypeScript frontend.

**Python:**
- No `pytest`, `unittest`, or any test runner in `requirements.txt`
- No `pytest.ini`, `setup.cfg`, `pyproject.toml`, or `conftest.py` files detected
- No test configuration of any kind

**Frontend (TypeScript/Vue):**
- No `vitest`, `jest`, `cypress`, or `playwright` in `agent_fronted/package.json`
- No test configuration files (`vitest.config.ts`, `jest.config.ts`, etc.)
- No `@vue/test-utils` or `@testing-library/vue` in dependencies

**Assertion Library:**
- None installed

**Run Commands:**
```bash
# No test commands exist. The package.json scripts are:
# npm run dev       - Start Vite dev server
# npm run build     - Build for production
# npm run preview   - Preview production build
```

## Test File Organization

**Location:**
- No test files exist anywhere in the codebase
- No `tests/`, `__tests__/`, `test/`, or `spec/` directories
- No `*.test.*` or `*.spec.*` files detected via globbing

**Naming:**
- Not established

**Structure:**
- Not established

## Test Types Present

- [ ] Unit tests
- [ ] Integration tests
- [ ] E2E tests
- [ ] Smoke tests

**Zero test coverage across the entire codebase.**

## Test Structure

Not applicable. No tests exist.

## Mocking

**Framework:** None

**Patterns:** Not established

## Fixtures and Factories

**Test Data:** None

**Location:** Not established

## Coverage

**Requirements:** None enforced

**View Coverage:**
```bash
# No coverage tooling installed
```

## Test Types

**Unit Tests:**
- Not present. The following areas would benefit from unit tests:
  - `tools.py`: `sanitize_for_json()`, `safe_json_dumps()`, `parse_todos_from_tool_output()`, `_normalize_status()`, `_extract_bracket_content()`
  - `knowledge_base.py`: `create_knowledge_base()` (with mocked embeddings/FAISS)
  - `prompt_template.py`: `get_identity_system_prompt()`
  - Frontend composables: `useTodosPanel.ts` (normalization, status mapping, summary building)
  - Frontend utils: `identityUtils.ts` (role parsing, display name generation)

**Integration Tests:**
- Not present. The following integration points lack testing:
  - FastAPI endpoints: `/chat/stream`, `/ai/history/{type}`, `/api/todos/{thread_id}`
  - Database connections (MySQL, PostgreSQL)
  - LangChain agent invocation pipeline
  - SSE streaming protocol compliance

**E2E Tests:**
- Not present. No Cypress, Playwright, or similar framework

## Manual Testing Evidence

The only testing mechanism is an interactive `__main__` block in `subagent/fault_explanation_agent.py`:
```python
if __name__ == "__main__":
    query = input("请输入问题：")
    while (query != 'q'):
        invoke_fault_explanation_agent(query)
        query = input("请输入问题：")
```
This is a manual REPL-style test harness for the sub-agent, not an automated test.

## Test Gaps

**Critical gaps (high risk of undetected regressions):**

1. **Tool output parsing** (`tools.py`): The `parse_todos_from_tool_output()` function handles multiple formats (dict, list, string, JSON, AST literal) with complex fallback logic. No tests validate these code paths.
   - Files: `tools.py` lines 460-583

2. **JSON sanitization** (`tools.py`): `sanitize_for_json()` recursively processes LangChain message objects, dicts, lists, datetimes, and arbitrary objects. Untested.
   - Files: `tools.py` lines 390-454

3. **SSE event protocol** (`app.py`): The `token_stream_events()` generator produces structured SSE events that the frontend depends on. Protocol changes would silently break the frontend.
   - Files: `app.py` lines 311-444, `agent_fronted/src/services/api.js`

4. **SQL query construction** (`subagent/call_api_tool.py`): Dynamic SQL with f-string interpolation is neither tested nor parameterized.
   - Files: `subagent/call_api_tool.py` lines 109-120

5. **Knowledge base creation** (`knowledge_base.py`): PDF loading, chunking, embedding, and FAISS indexing pipeline has no tests.
   - Files: `knowledge_base.py` lines 13-103

6. **Frontend composable logic**: `useTodosPanel.ts` contains status normalization, summary building, and auto-hide timer logic -- all pure logic suitable for unit testing.
   - Files: `agent_fronted/src/composables/useTodosPanel.ts`

7. **User identity resolution**: Both backend (`prompt_template.py`) and frontend (`useChatStream.ts` `resolveUserIdentity()`, `identityUtils.ts`) have identity mapping logic that should be tested for edge cases.
   - Files: `prompt_template.py` lines 82-87, `agent_fronted/src/composables/useChatStream.ts` lines 132-154, `agent_fronted/src/utils/identityUtils.ts`

## Recommended Test Setup

**Python backend (if adding tests):**
```bash
# Add to requirements.txt:
pytest==8.x.x
pytest-asyncio==0.x.x
httpx==0.28.1  # already present, use for FastAPI TestClient

# Create:
# tests/
# tests/conftest.py
# tests/test_tools.py
# tests/test_app.py
# tests/test_knowledge_base.py

# Run:
pytest tests/ -v
```

**Frontend (if adding tests):**
```bash
# Add to package.json devDependencies:
# vitest, @vue/test-utils, @testing-library/vue

# Create:
# agent_fronted/src/__tests__/
# agent_fronted/vitest.config.ts

# Run:
# npx vitest
```

---

*Testing analysis: 2026-03-26*
