# Pitfalls Research

**Domain:** Python AI Agent Framework Modularization (LangChain 1.0.3 + LangGraph 1.0.5 + FastAPI)
**Researched:** 2026-03-26
**Confidence:** HIGH (grounded in actual codebase analysis from CONCERNS.md + verified LangChain 1.0 documentation)

---

## Critical Pitfalls

### Pitfall 1: Breaking the Working Agent During Refactoring (Zero Test Coverage)

**What goes wrong:**
The refactoring moves code across files and introduces new abstractions. Without any tests, there is no automated way to verify the existing fault-diagnosis agent still works after each change. Silent regressions — broken SSE streams, middleware no longer firing, PostgreSQL checkpoint failures — only surface at manual smoke test time or in production.

**Why it happens:**
The codebase currently has zero test files (confirmed in CONCERNS.md). Developers underestimate how much implicit coupling exists in a 592-line monolith. Moving `fig_inter` from `app.py` into a shared module looks trivial, but it relies on `globals()` state mutations that break silently when the namespace changes.

**How to avoid:**
Before touching any code, write characterization tests that capture current behavior:
1. Record current SSE event sequence for a sample query (event types + approximate order).
2. Snapshot the API contract: `/chat/stream`, `/ai/history/{type}`, `/api/todos/{thread_id}` — expected status codes and response shapes.
3. Write at least one integration smoke test using `httpx.AsyncClient` against the FastAPI `TestClient`.

Refactor in small increments: move one module, run the smoke test, commit. Never leave refactoring in a half-done state across multiple files simultaneously.

**Warning signs:**
- A refactoring phase has no "verify existing behavior" task in its acceptance criteria.
- Changes touch more than two files without a test run between them.
- "It should still work" is said without running the application.

**Phase to address:**
The very first phase of the milestone must be "Write characterization tests before any code moves." Every subsequent phase includes "smoke tests pass" as a gate condition.

---

### Pitfall 2: LangChain 1.0 `middleware` + `context_schema` Mutual Exclusion Trap

**What goes wrong:**
In LangChain 1.0, `create_agent()` had a hard assertion preventing simultaneous use of `middleware` and a custom `state_schema`. The current `app.py` uses both `middleware=[TodoListMiddleware, SummarizationMiddleware, dynamic_prompt_fn]` and `context_schema=Context`. A modular framework that lets new projects configure arbitrary middleware + context schemas will hit this incompatibility if the code path is wrong.

**Why it happens:**
The original monolith works because the specific combination was tested and the assertion may have been fixed by October 2025 (`create_agent` now allows `state_schema` directly per GitHub issue #33217). But during modular refactoring, if someone uses an older code path or the middleware registration order changes, the assertion fires at agent creation time — which happens at app startup, causing an immediate `AssertionError` with no graceful recovery.

**How to avoid:**
- Pin the exact LangChain 1.0.3 behavior: verify with the actual installed version that `create_agent(middleware=[...], context_schema=...)` works together in a minimal script before extracting the factory into a module.
- In the modular `AgentFactory`, validate the combination before calling `create_agent()` and raise a `ConfigurationError` with a human-readable message rather than letting the LangChain assertion bubble up.
- Document the constraint in the framework's public interface: "If using `context_schema`, middleware must be compatible middleware classes, not raw `@dynamic_prompt` decorators."

**Warning signs:**
- `AssertionError` at application startup after extracting `create_agent()` into a factory module.
- Middleware list works in isolation but fails when combined with context schema.
- New project using the framework omits context schema and works, but enabling it causes startup crash.

**Phase to address:**
The Agent Factory extraction phase. The factory module's first test must create an agent with all three: middleware list, dynamic prompt, and context schema together.

---

### Pitfall 3: Module-Level Database Connection Causing Import-Time Failures

**What goes wrong:**
`tools.py` creates a live `SQLDatabase` instance and `SQLDatabaseToolkit` at import time (lines 33-37). When `tools.py` is split into a framework core module + project-specific tools module, any file that imports from the project tools triggers a MySQL connection attempt at import time. If MySQL is down (deployment startup race condition, test environment, new project that does not use MySQL), the import fails with a connection error and the entire application refuses to start.

**Why it happens:**
The original monolith tolerated this because MySQL was always available when `app.py` was imported. Modularization makes this assumption visible: a framework core that imports project tools at startup inherits all of the project tools' dependencies.

**How to avoid:**
Move all database connections out of module-level code into lazy initialization functions or FastAPI lifespan:
```python
# BAD — current pattern
db = SQLDatabase.from_uri(db_url)  # module level

# GOOD — lazy init in lifespan
async def lifespan(app: FastAPI):
    app.state.db = SQLDatabase.from_uri(db_url)
    yield
    # cleanup
```
Tools that need database access should receive it via dependency injection (passed as a parameter or accessed from `app.state`), not via module-level globals.

Also centralize `load_dotenv()` to exactly one call in the entry point. Currently called 7+ times across files — modularization will multiply this.

**Warning signs:**
- `ImportError` or `OperationalError` when running tests that import any tool module.
- Application startup fails if MySQL is temporarily unavailable.
- `load_dotenv()` called inside tool functions (not just at module level).

**Phase to address:**
The Tools modularization phase. Acceptance criteria must include: "Tool modules can be imported in a test environment without a database connection."

---

### Pitfall 4: Import Cycle When Splitting the Monolith

**What goes wrong:**
`app.py` currently defines tools (`fig_inter`, `extract_data`) that use FastAPI's `app.state`. If these tools are extracted to `core/tools/` and the core module also imports from `core/agent_factory.py` which imports from FastAPI app — a circular import chain forms: `app.py` → `core/tools/` → `core/agent_factory.py` → `app.py`.

Python's import system partially executes a module the first time it is imported. A circular import causes `AttributeError` or `ImportError` where a name exists in the file but is `None` at the time another module tries to use it.

**Why it happens:**
The monolith has no boundaries — `fig_inter` in `app.py` uses `globals()` which implicitly scopes it to the app module. When extracted, the tool needs to know "which app" and reaches back toward `app.py`, creating the cycle.

**How to avoid:**
Enforce a strict dependency hierarchy before writing any code:
```
Entry point (app.py / main.py)
    └── Project layer (project/tools.py, project/prompts.py)
            └── Core framework (core/agent_factory.py, core/middleware/)
                    └── Shared utilities (core/utils/, core/db/)
```
Nothing in `core/` may import from `project/` or from `app.py`. Nothing in `project/` may import from `app.py`. Use `TYPE_CHECKING` blocks for type hints that would otherwise create cycles.

For `fig_inter` specifically: the tool should not reference `globals()`. Instead, it should use a local namespace dict passed as a parameter or stored in a request-scoped context object.

**Warning signs:**
- `ImportError: cannot import name 'X' from partially initialized module` on startup.
- A module appears in `sys.modules` but its attributes are `None`.
- Moving an import from module-level to inside a function "fixes" the error (band-aid symptom of a real cycle).

**Phase to address:**
Project structure definition phase (the first code phase). Document the dependency hierarchy in a `ARCHITECTURE.md` before any files are moved.

---

### Pitfall 5: Over-Abstraction — Building a Framework Nobody Uses

**What goes wrong:**
The goal is "other developers can build new Agent projects." In practice, this means the framework gets designed for hypothetical future projects rather than the one real project that exists: the fault-diagnosis system. The refactoring produces a highly generic `AgentBuilder`, `MiddlewareRegistry`, `ToolRegistry`, `KnowledgeBaseAdapter`, `PromptComposer`, and a YAML config schema — but the fault-diagnosis project is the only consumer and it uses 20% of the features.

The resulting codebase is harder to understand than the original `app.py`, and any future developer still has to read all the abstraction layers to understand what runs.

**Why it happens:**
Refactoring to "generic framework" invites gold-plating. Each extracted component gets an interface "just in case" a second implementation is needed. The requirement says "new projects only need a thin wrapper" which sounds like it requires many layers of abstraction, but it actually just requires good separation of concerns.

**How to avoid:**
Apply the "rule of three": only abstract when the third concrete use case appears. For this milestone, there is one real project. The framework should be designed as if a second project is coming, but not as if ten are.

Concrete constraint: no abstraction layer that is not exercised by the fault-diagnosis project itself. Every `class FooInterface(ABC)` must have at least one concrete implementation in the codebase, tested.

The entry-point goal is achievable with simple Python modules and a config dict — not a plugin registry system. Prefer explicit function calls over metaclass magic.

**Warning signs:**
- A new abstract base class is defined but has only one subclass.
- The "thin wrapper" entry point still requires importing 8+ framework modules.
- Config schema has more than 15 keys for the first project.
- "We might need this later" said during code review.

**Phase to address:**
Every phase. Each phase's acceptance criteria should include: "The fault-diagnosis agent still starts and works with the new structure." If a refactored module makes the working system harder to read without making it easier to extend, reconsider the abstraction.

---

### Pitfall 6: Configuration Explosion — YAML/Config Options That Are Never Used

**What goes wrong:**
"Configuration-driven assembly" (PROJECT.md requirement) is interpreted as building a comprehensive config schema that covers every possible variation. The config file ends up with sections for `embedding.provider`, `embedding.model`, `embedding.base_url`, `embedding.dimension`, `embedding.batch_size`, `embedding.retry_policy`, `embedding.timeout` — when the project only ever uses Ollama with one model on one URL.

Unused config options increase cognitive load for every future developer who reads the config file and wonders what each option does and whether it matters.

**Why it happens:**
The config schema is designed top-down ("what might anyone ever configure?") rather than bottom-up ("what does the fault-diagnosis project actually need to configure?"). Hardcoded values in `knowledge_base.py` (Ollama URL, model name, FAISS path) are correctly identified as needing to be configurable — but this gets extended to "everything should be configurable."

**How to avoid:**
Start with the minimum config that removes the hardcoded values identified in CONCERNS.md:
- `OLLAMA_BASE_URL`
- `EMBEDDING_MODEL_NAME`
- `FAISS_PATH`
- MySQL connection parameters
- PostgreSQL connection parameters
- LLM model name

That is the initial config surface. New options are added only when a concrete second project needs them. Use environment variables (`.env`) as the config mechanism — not YAML — because the project already uses `python-dotenv`.

**Warning signs:**
- Config schema documents options that `knowledge_base.py` does not currently read.
- A config file section is added "for future use."
- The config validation code is longer than the code that uses the config values.

**Phase to address:**
Configuration layer phase. Scope: "Move existing hardcoded values to env vars. Nothing more."

---

## Technical Debt Patterns

Shortcuts that seem reasonable but create long-term problems.

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Keep `app_copy.py` during refactoring as a fallback | Psychological safety net | Two diverging code paths; bugs fixed in one not the other | Delete it before the first refactoring commit |
| Module-level `load_dotenv()` in every new module | Easy env access | Env loaded 10+ times; mid-run `.env` changes cause inconsistency | Never; centralize to entry point only |
| Copy tool definitions into each project rather than importing from core | Avoids import complexity | Duplicated logic; bugs fixed in one copy but not others | Never; the duplication is exactly what this refactoring must end |
| Use `globals()` in extracted tools to share state | Matches current behavior | Cross-request state leak; untestable; non-reentrant | Never; replace with explicit context passing |
| Skip connection pooling during refactoring ("we'll fix it later") | Faster refactoring | MySQL creates new connection per tool call; 5-10 calls per agent turn | Acceptable in Phase 1, must be fixed before calling the framework "production-ready" |
| Inline middleware config in the project entry file | Avoids config abstraction | Each new project must read the example project to understand options | Acceptable for first project; document the pattern clearly |

---

## Integration Gotchas

Common mistakes when working with the specific integrations in this codebase.

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| AsyncPostgresSaver | Creating pool without `autocommit=True` and `row_factory=dict_row` | Always set both; `setup()` requires autocommit to commit checkpoint tables |
| AsyncPostgresSaver | Upgrading `langgraph-checkpoint-postgres` version — schema column `cw.task_path` was added, breaking older installs | Do not upgrade the checkpoint-postgres package during this refactoring; pin to current version |
| FAISS load | `allow_dangerous_deserialization=True` is already present; extracting to a config module may accidentally omit it | Keep this flag explicit in the KnowledgeBase loading code; do not hide it behind a "safe defaults" wrapper |
| LangChain `@tool` decorator | Moving tool definitions to a new module breaks if the tool's `args_schema` Pydantic model is defined in the old module and imported — Pydantic v2 schema namespacing can cause `ValidationError` | Keep each tool's schema class in the same module as the tool function |
| LangChain `@dynamic_prompt` | Decorator applies at import time; if the decorated function is imported lazily, the prompt injection does not fire | Ensure `@dynamic_prompt` functions are imported eagerly during agent factory initialization |
| Ollama embedding | Hardcoded `http://10.108.13.254:11434` — if URL is wrong at FAISS load time, `knowledge_base.py` fails silently or errors at search time, not at startup | Add a health-check ping to the Ollama endpoint during lifespan startup; fail fast |
| FastAPI `app.state` | Accessing `app.state.agent` inside a tool function creates an implicit dependency on FastAPI — tools become untestable without running a full app | Pass agent or shared resources to tools via closure parameters or a `ToolContext` dataclass, not via `app.state` |

---

## Performance Traps

Patterns that work at small scale but cause problems under concurrent load.

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| New MySQL connection per tool call | High latency on multi-tool agent turns (5-10 SQL calls = 5-10 TCP handshakes) | Create a shared `asyncpg` or `SQLAlchemy` pool in lifespan, inject into tools | Any agent turn with more than 2-3 tool invocations |
| Matplotlib in the async event loop | Chart generation blocks other SSE streams under concurrent users | Move `fig_inter` execution to `asyncio.run_in_executor` with a thread pool | 2+ concurrent users requesting charts |
| O(n) checkpoint scan for history endpoint | `/ai/history/all` gets slower as conversations accumulate | Maintain a separate thread-ID index table, or add pagination | ~500+ conversations in the database |
| Sub-agent created fresh per invocation | `fault_explanation_tool` creates a new LangGraph agent (with its own model client) on every call | Cache the sub-agent or its compiled graph in `app.state` | High-frequency invocation of the fault explanation tool |
| FAISS loaded from disk on every rebuild | `rebuild_kb.py` blocks during index rebuild if run in-process | Keep FAISS load as a one-time startup operation; rebuild is a separate offline process | Not a runtime concern; but rebuilding while server is running causes lock contention |

---

## Security Mistakes

Security issues specific to this refactoring context (beyond general security; the existing critical issues in CONCERNS.md are pre-existing).

| Mistake | Risk | Prevention |
|---------|------|------------|
| Copying hardcoded API keys from `app_copy.py` into new modules during "extract shared code" | Keys propagate further into git history | Delete `app_copy.py` before any refactoring starts; rotate exposed keys |
| New config module that reads `.env` and logs its values at startup for debugging | LLM API keys appear in server logs | Never log config values directly; log only `"Config loaded: MODEL_NAME=***"` |
| Extracting `python_inter` tool into the "core framework" as a reusable capability | Any project using the framework inherits arbitrary code execution with no sandbox | Keep `python_inter` in the project-specific tools layer with an explicit warning; do not promote to core |
| Plugin/tool registration system that accepts callable objects from config files | Arbitrary code execution via config-injected callables | Tool registration must only accept pre-defined tool names or module paths; validate against an allowlist |
| SQL injection in `subagent/call_api_tool.py` is carried forward into refactored module | Crafted `table_name` executes arbitrary SQL | Fix the SQL injection before or during the subagent modularization phase, not after |

---

## "Looks Done But Isn't" Checklist

Things that appear complete during this refactoring but have critical missing pieces.

- [ ] **Tool modularization:** Tools appear to work in isolation — verify they work when called by the LangGraph agent in a full conversation turn (not just unit-tested in isolation).
- [ ] **Middleware modularization:** Middleware list is configurable — verify that removing `SummarizationMiddleware` from a project's config actually prevents it from running (not just excluded from the list while still imported).
- [ ] **Knowledge base modularization:** FAISS path is configurable — verify that the knowledge base actually loads from the configured path, not from a hardcoded fallback in `knowledge_base.py`.
- [ ] **Entry point simplification:** New project entry file is "thin" — verify it does not need to copy-paste middleware setup code from the original `app.py`.
- [ ] **Existing project compatibility:** The refactored fault-diagnosis project passes smoke tests — verify SSE streaming still works end-to-end (token events, tool_start, tool_end, complete events all firing in correct order).
- [ ] **Sub-agent integration:** `fault_explanation_tool` still works after subagent modularization — verify the `import os` missing bug (CONCERNS.md) is fixed, not just worked around.
- [ ] **PostgreSQL checkpoint persistence:** Conversation history persists across server restarts after lifespan refactoring — verify by restarting the server mid-conversation.
- [ ] **Frontend API contract unchanged:** All API URLs, query parameter names, SSE event names, and response shapes are identical to pre-refactoring — verify by running the Vue frontend against the refactored backend.

---

## Recovery Strategies

When pitfalls occur despite prevention, how to recover.

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Import cycle discovered mid-refactoring | MEDIUM | Revert the last 1-2 commits. Map the full import dependency graph before proceeding. Extract a shared `core/types.py` with pure data types (no business logic) that all modules can import without cycles. |
| middleware + context_schema incompatibility at startup | LOW | Revert to passing context schema directly without going through the modular factory. File a comment in code explaining the constraint. |
| Existing fault-diagnosis agent broken by refactoring | HIGH | Use git to identify the exact commit that broke behavior. Extract a failing test from the smoke test results. Fix incrementally. Never "fix forward" by guessing — bisect to the breaking change. |
| Configuration explosion already happened | MEDIUM | Delete all config options that are not read by any code in the project. Run `grep -r "config\." .` to find all actual usages. Keep only those. |
| Circular database connection at import time | LOW | Add a `_db: Optional[SQLDatabase] = None` lazy init pattern with a `get_db()` accessor. Call `get_db()` inside tool functions, not at module level. |
| `app_copy.py` was used as base for refactoring (wrong file) | HIGH | All work derived from `app_copy.py` must be redone against `app.py`. There is no shortcut — `app_copy.py` lacks `SummarizationMiddleware` and has stale API keys. |

---

## Pitfall-to-Phase Mapping

How roadmap phases should address these pitfalls.

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| Breaking the working agent (no tests) | Phase 1: Characterization Tests | Smoke tests pass before any file is moved |
| middleware + context_schema incompatibility | Phase 2: Agent Factory Extraction | Factory creates agent with all three: middleware + dynamic_prompt + context_schema |
| Module-level DB connection at import time | Phase 3: Tools Modularization | Tool modules importable in test environment with no DB running |
| Import cycle from monolith split | Phase 2: Project Structure Definition | `python -c "from core.agent_factory import create_agent"` succeeds with no circular import warning |
| Over-abstraction / framework trap | All phases (ongoing) | Each phase: count abstractions with zero or one concrete implementation; target is zero |
| Configuration explosion | Phase 4: Configuration Layer | Config schema has exactly as many keys as `grep -r "os.getenv" .` finds hardcoded values |
| MySQL connection per tool call | Phase 3: Tools Modularization (note) | Marked as known debt; fixed before "production-ready" milestone gate |
| Matplotlib in event loop | Phase 3: Tools Modularization (note) | Marked as known debt; `fig_inter` wrapped in `run_in_executor` if time permits |
| API keys in git history | Phase 1 (pre-work) | `app_copy.py` deleted; keys rotated; `git log --all -S "sk-"` returns no results |
| SQL injection carried forward | Phase 3: Subagent Modularization | Parameterized queries in refactored subagent tool |
| Frontend API contract broken | Final phase: Integration | Vue frontend smoke test: send a message, receive SSE stream, check todos endpoint |

---

## Sources

- Codebase analysis: `.planning/codebase/CONCERNS.md` (2026-03-26) — direct observation of existing bugs and patterns
- LangChain GitHub issue: [`middleware` and `state_schema` are mutually exclusive in `create_agent()` · Issue #33217](https://github.com/langchain-ai/langchain/issues/33217) — confirmed incompatibility and workaround
- LangGraph checkpoint: [langgraph-checkpoint-postgres version update issue · Issue #3557](https://github.com/langchain-ai/langgraph/issues/3557) — schema breaking change between versions
- LangGraph AsyncPostgresSaver: [`AsyncPostgresSaver` psycopg errors · Issue #2755](https://github.com/langchain-ai/langgraph/issues/2755) — `autocommit=True` and `row_factory=dict_row` requirements
- Python circular imports: [The Circular Import Problem: Breaking Dependency Cycles — DEV Community](https://dev.to/aaron_rose_0787cc8b4775a0/the-circular-import-problem-breaking-dependency-cycles-4i56) — structural prevention strategies
- Refactoring without tests: [Strangler Fig Pattern — Shopify Engineering](https://shopify.engineering/refactoring-legacy-code-strangler-fig-pattern) — incremental migration, characterization tests
- FastAPI lifespan DI: [FastAPI Dependency Injection in Lifespan · Discussion #11742](https://github.com/fastapi/fastapi/discussions/11742) — startup state patterns and limitations
- LangChain 1.0 middleware: [Agent Middleware — LangChain Blog](https://blog.langchain.com/agent-middleware/) — middleware execution order and hooks
- Modular monolith over-abstraction: [Structuring Modular Monoliths — DEV Community](https://dev.to/xoubaman/modular-monolith-3fg1) — shared code coupling patterns

---
*Pitfalls research for: LangChain 1.0.3 + LangGraph 1.0.5 + FastAPI monolith modularization*
*Researched: 2026-03-26*
