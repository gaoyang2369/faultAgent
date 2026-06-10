# Codebase Concerns

**Analysis Date:** 2026-03-26

## Critical Issues

### Hardcoded API Keys Committed to Git
- **Severity**: Critical
- **Location**: `app_copy.py:222`, `app_copy.py:229`, `subagent/fault_explanation_agent.py:19`, `subagent/fault_explanation_agent.py:25`
- **Description**: Multiple API keys are hardcoded in commented-out code blocks that are committed to the repository. These include `sk-gxqf2qxERmrrX9RUgM5Oqsbz9GBJkifzRymoQSGaiZW5Dbyg`, `sk-5hmjTgNvEwpzxliLy8Ub6SBkjdt5GkotJUcr9Y8HoW8CQ7bX`, and ModelScope keys `ms-41875842-...`, `ms-2fcce8b4-...`. Even though they are commented out, they are in git history.
- **Impact**: Anyone with repository access can extract valid API keys. Keys may be exploited for unauthorized LLM usage, incurring cost or data leakage.
- **Suggested fix**: Rotate all exposed keys immediately. Remove commented-out blocks containing secrets. Use `git filter-branch` or BFG Repo Cleaner to purge from history. Enforce pre-commit hooks to scan for secret patterns.

### Arbitrary Code Execution via `eval()`/`exec()` Without Sandboxing
- **Severity**: Critical
- **Location**: `app.py:79` (`eval`), `app.py:83` (`exec`), `app.py:180` (`exec`), `subagent/call_api_tool.py:209` (`exec`)
- **Description**: The `python_inter` and `fig_inter` tools execute arbitrary Python code supplied by the LLM agent using `eval()` and `exec()` on the server's global namespace. There is zero sandboxing, no allowlist of permitted operations, and no resource limits.
- **Impact**: A prompt injection attack or misbehaving LLM could execute arbitrary system commands (e.g., `os.system("rm -rf /")`, `subprocess.call(...)`, file reads of secrets). The `globals()` namespace is shared, so code can modify any server state.
- **Suggested fix**: At minimum, run user/LLM-generated code in a restricted subprocess with timeout and resource limits (e.g., `RestrictedPython`, Docker container, or Pyodide sandbox). Restrict available builtins and modules. Consider removing `python_inter` entirely since it is marked "disabled" but still defined.

### SQL Injection in Subagent Tool
- **Severity**: Critical
- **Location**: `subagent/call_api_tool.py:101-118`
- **Description**: The `query_fault_data_and_call_api` function constructs SQL queries by directly interpolating `table_name`, `start_time`, `end_time`, and `limit` parameters into f-strings without parameterized queries or input validation.
- **Impact**: A crafted `table_name` like `data_J3; DROP TABLE data_J3;--` or a `start_time` with injected SQL could execute arbitrary database commands including data deletion.
- **Suggested fix**: Validate `table_name` against an allowlist of known tables. Use parameterized queries for `start_time`/`end_time`. Cast `limit` to int explicitly before interpolation.

### No Authentication or Authorization on API Endpoints
- **Severity**: Critical
- **Location**: `app.py:446-473` (all endpoints)
- **Description**: All API endpoints (`/chat/stream`, `/ai/history/{type}`, `/ai/history/{type}/{chat_id}`, `/api/todos/{thread_id}`) are publicly accessible without any authentication. The `user_identity` parameter is a simple query string value ("游客" or "管理员") that the client self-reports.
- **Impact**: Any user can claim to be "管理员" by passing `user_identity=管理员` in the URL. Any user can read any thread's chat history and todo list by guessing/iterating `thread_id` values. The chat endpoint can be abused to consume LLM API credits.
- **Suggested fix**: Implement proper authentication (JWT, session tokens, or API keys). Validate `user_identity` server-side based on authenticated session. Add rate limiting per client/IP.

## Security Concerns

- **Wildcard CORS policy**: `app.py:303-309` -- `allow_origins=["*"]` with `allow_credentials=True` allows any origin to make authenticated requests. Risk: Medium. Fix: Restrict to specific frontend origins.
- **FAISS dangerous deserialization**: `knowledge_base.py:31` -- `allow_dangerous_deserialization=True` enables pickle deserialization of FAISS index files. If an attacker can replace the `faiss_db/` files, arbitrary code execution is possible. Risk: Medium.
- **Hardcoded internal IP addresses**: `knowledge_base.py:26,75` (`http://10.108.13.254:11434`), `subagent/call_api_tool.py:91` (`http://10.108.13.250:8001/predict_reason`) -- Internal network topology is exposed in source code. Risk: Low (information disclosure).
- **User message passed via GET query parameter**: `app.py:447` -- Chat messages are sent as GET query parameters, which means they appear in server logs, proxy logs, and browser history. Long messages may also exceed URL length limits. Risk: Medium. Fix: Use POST endpoint for message submission.
- **HTML report template injection**: `tools.py:366-373` -- The `save_html_report` tool uses simple string `.replace()` to inject LLM-generated HTML content into the template. There is no sanitization, so the LLM (or a prompt injection) could inject malicious JavaScript into generated reports. Risk: Medium.

## Performance Concerns

- **New database connection per tool call (no connection pooling)**: `tools.py:108-142`, `subagent/call_api_tool.py:95-163` -- Both `sql_inter` and `query_fault_data_and_call_api` create a fresh `pymysql.connect()` for every single invocation, then close it. Similarly, `extract_data` in `app.py:121` creates a new SQLAlchemy engine per call. This incurs TCP handshake + auth overhead on every tool invocation.
  - Impact: High latency for sequential tool calls during a single agent turn (could invoke SQL tools 5-10 times per conversation).
  - Fix: Create a shared connection pool at startup (e.g., SQLAlchemy engine with pool) and inject it into tools.

- **`load_dotenv(override=True)` called repeatedly**: `tools.py:22,31,101`, `app.py:38,112`, `subagent/call_api_tool.py:19`, `subagent/fault_explanation_agent.py:10` -- Environment variables are re-read from disk on nearly every tool invocation. This is wasteful and can cause subtle bugs if `.env` changes mid-execution.
  - Impact: Low (I/O overhead, potential inconsistency).
  - Fix: Call `load_dotenv()` once at startup in a single entry point.

- **Chat history endpoint iterates all checkpoints**: `app.py:480-487` -- `get_chat_history` uses `async for checkpoint_tuple in request.app.state.checkpointer.alist()` which streams through ALL checkpoints in the database to collect unique thread IDs. This is O(n) in the number of checkpoints.
  - Impact: As the system accumulates conversations, this endpoint will become increasingly slow.
  - Fix: Maintain a separate index/table of thread IDs, or add pagination.

- **Module-level database connection in `tools.py`**: `tools.py:33-37` -- An `SQLDatabase` instance and `SQLDatabaseToolkit` are created at import time with a live database connection. This blocks import and can cause startup failures if the database is temporarily unavailable.
  - Impact: Medium. Application fails to start if MySQL is down during import.

- **Global namespace pollution from `exec()`/`globals()`**: `app.py:77,125,179-181` -- `python_inter`, `extract_data`, and `fig_inter` all write to `globals()`, meaning every executed code snippet and every extracted DataFrame persists in server memory indefinitely, leaking across requests and users.
  - Impact: Memory leak grows unbounded over time. Data isolation violation between users/sessions.

## Maintainability Issues

- **`app_copy.py` is a stale copy of `app.py`**: `app_copy.py` (581 lines) is nearly identical to `app.py` (591 lines) with minor differences: it has hardcoded API keys, lacks `SummarizationMiddleware`, and includes `python_inter` in the tool list. This creates confusion about which file is authoritative.
  - Fix: Delete `app_copy.py` or clearly document its purpose. Use git branches for experimental variants.

- **Duplicate `fig_inter` tool definition**: The `fig_inter` tool is defined in three places: `app.py:140-199`, `app_copy.py:140-199`, and `subagent/call_api_tool.py:166-228`. The implementations are nearly identical with minor variations in available local variables (`sns` vs `np`).
  - Fix: Extract `fig_inter` into a shared utility module and import it where needed.

- **Duplicate database connection logic**: Database connection setup (reading env vars, constructing connection strings) is duplicated across `tools.py:101-110`, `tools.py:33-37`, `subagent/call_api_tool.py:19-25`, `app.py:112-121`, and `app.py:239`.
  - Fix: Create a single `db.py` module that provides connection factory/pool functions.

- **Hardcoded database name "dcma"**: `tools.py:35` -- The `db_name` for the DCMA SQL toolkit is hardcoded as `"dcma"` instead of using an environment variable like the other database configurations.
  - Fix: Move to env var `DCMA_DB_NAME` for consistency.

- **Missing `import os` in subagent**: `subagent/fault_explanation_agent.py:34-38` -- The `create_fault_explanation_agent()` function calls `os.getenv()` but the file never imports `os`. This will cause a `NameError` at runtime.
  - Impact: The fault explanation sub-agent cannot be created at all, making the `fault_explanation_tool` in `tools.py` non-functional.
  - Fix: Add `import os` at the top of `subagent/fault_explanation_agent.py`.

- **Path parameter `type` shadows Python builtin**: `app.py:477` -- The endpoint `get_chat_history(request, type: str)` uses `type` as a parameter name, shadowing the Python builtin. While not a bug, it reduces code clarity.

- **Commented-out code throughout codebase**: Multiple files contain extensive commented-out blocks of alternative model configurations and old code: `app.py:204-218`, `app_copy.py:206-232`, `subagent/fault_explanation_agent.py:16-33`. This clutters the codebase.
  - Fix: Remove commented-out code. Use git history for version tracking.

- **Hardcoded Ollama embedding model URL**: `knowledge_base.py:26,75` -- The Ollama embedding service URL (`http://10.108.13.254:11434`) and model name (`qwen3-embedding:8b`) are hardcoded rather than configurable via environment variables.
  - Fix: Move to env vars `OLLAMA_BASE_URL` and `EMBEDDING_MODEL_NAME`.

## Technical Debt

| Item | Location | Severity | Effort to Fix |
|------|----------|----------|---------------|
| Hardcoded API keys in git history | `app_copy.py`, `subagent/fault_explanation_agent.py` | Critical | Medium (rotate keys + scrub history) |
| No code execution sandboxing | `app.py:79,83,180` | Critical | High (requires sandbox infrastructure) |
| SQL injection in subagent | `subagent/call_api_tool.py:101-118` | Critical | Low (parameterize queries, validate table names) |
| No authentication on any endpoint | `app.py` (all routes) | Critical | Medium (add auth middleware) |
| Missing `import os` in subagent | `subagent/fault_explanation_agent.py` | High | Trivial (one line) |
| Stale `app_copy.py` | `app_copy.py` | Medium | Trivial (delete file) |
| No connection pooling for tools | `tools.py:108`, `subagent/call_api_tool.py:95` | Medium | Medium (refactor to shared pool) |
| Duplicate `fig_inter` across 3 files | `app.py`, `app_copy.py`, `subagent/call_api_tool.py` | Medium | Low (extract to shared module) |
| Globals() memory leak | `app.py:77,125,179` | Medium | Medium (use isolated namespaces per request) |
| Hardcoded internal IPs | `knowledge_base.py:26,75`, `subagent/call_api_tool.py:91` | Low | Trivial (move to env vars) |
| Repeated `load_dotenv()` calls | 7+ locations across all files | Low | Low (centralize) |
| 500KB `api_style.md` in subagent | `subagent/api_style.md` (18,479 lines) | Low | Low (consider if needed in repo) |

## Dependency Risks

- **Large dependency surface**: `requirements.txt` lists 40+ packages including heavy ones like `scipy`, `scikit-learn`, `weasyprint`, `redis`, `aiomysql`, `aiosqlite` that do not appear to be used in the current codebase. Unused dependencies increase attack surface, build time, and image size.
  - Unused candidates: `redis` (no Redis usage found), `aiomysql` (using `pymysql` directly), `aiosqlite` (no SQLite usage), `weasyprint` (no PDF generation from HTML), `scipy`/`scikit-learn` (no ML code in main app), `openpyxl` (no Excel handling), `markdown`/`markdown2` (no Markdown-to-HTML conversion in Python code).
  - Fix: Audit imports and remove unused packages.

- **No version pinning for transitive deps**: `requirements.txt` uses exact versions for direct dependencies but has no lock file. Transitive dependencies are not pinned, which can cause non-reproducible builds.

- **`sse-starlette` listed but not imported**: `requirements.txt:4` lists `sse-starlette==2.1.3` but the code implements SSE manually via `StreamingResponse` in FastAPI.

## Scalability Concerns

- **In-process matplotlib rendering**: `app.py:167-199`, `subagent/call_api_tool.py:196-228` -- Chart generation runs synchronously in the main event loop via `exec()`. Matplotlib is not thread-safe, and `matplotlib.use('Agg')` / `matplotlib.use(current_backend)` switching in the `finally` block is not safe under concurrent requests.
  - Impact: Under concurrent load, charts may interfere with each other or crash.
  - Fix: Move chart generation to a worker process/thread pool.

- **Single-process architecture**: The server runs as a single uvicorn process (`app.py:586-591`). There is no worker configuration for multi-process or multi-worker deployment.
  - Impact: Cannot scale beyond one CPU core; a blocking tool call stalls all other requests.
  - Fix: Use `uvicorn` with `--workers` or deploy behind `gunicorn` with uvicorn workers.

- **No cleanup for generated files**: Images (`agent_fronted/public/images/`) and reports (`agent_fronted/public/reports/`) accumulate on disk indefinitely with no cleanup mechanism, TTL, or size limit.

## Code Duplication

- **`app.py` vs `app_copy.py`**: These two files share ~95% identical code (581 vs 591 lines). The only meaningful differences are model configuration and `SummarizationMiddleware`.
  - Files: `app.py`, `app_copy.py`

- **`fig_inter` tool**: Defined independently in `app.py:140-199`, `app_copy.py:140-199`, and `subagent/call_api_tool.py:166-228` with near-identical logic.
  - Files: `app.py`, `app_copy.py`, `subagent/call_api_tool.py`

- **Database connection setup**: Environment variable reading + connection construction repeated in `tools.py:31-37`, `tools.py:101-110`, `subagent/call_api_tool.py:19-25`, `app.py:112-121`, `app.py:239`.
  - Files: `tools.py`, `subagent/call_api_tool.py`, `app.py`

- **`extract_data` tool**: Defined identically in both `app.py:99-133` and `app_copy.py:99-133`.
  - Files: `app.py`, `app_copy.py`

## Test Coverage Gaps

- **No tests exist**: There are zero test files in the entire project. No unit tests, no integration tests, no end-to-end tests.
  - Files: entire codebase
  - Risk: Any refactoring or feature addition could silently break existing functionality. The SQL injection, code execution, and serialization bugs described above would be caught by even basic test coverage.
  - Priority: High
  - Suggested approach: Start with unit tests for `tools.py` utility functions (`sanitize_for_json`, `safe_json_dumps`, `parse_todos_from_tool_output`, `_normalize_status`), then add integration tests for the API endpoints.

---

*Concerns audit: 2026-03-26*
