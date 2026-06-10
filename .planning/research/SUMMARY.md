# Project Research Summary

**Project:** fault-diagnosis — Modular Python AI Agent Framework Refactoring
**Domain:** Python AI Agent Framework (LangChain 1.0 / LangGraph 1.0 / FastAPI)
**Researched:** 2026-03-26
**Confidence:** HIGH

## Executive Summary

This is a **refactoring project**, not a greenfield build. The codebase is a 592-line monolithic `app.py` that must be split into a reusable `agent_core/` framework and a pluggable `projects/fault_diagnosis/` domain layer — all within fixed dependency constraints (LangChain 1.0.3, LangGraph 1.0.5, FastAPI 0.121.0, Python 3.12). The goal is that a second agent project can be started by writing only a thin `main.py` + domain-specific tools, without touching framework code. The recommended architecture achieves this through three patterns already native to LangChain 1.0: a dict-based ToolRegistry, a composable middleware list, and a pydantic-settings config model backed by TOML. No new heavyweight dependencies are needed.

The recommended refactoring approach mirrors the Strangler Fig pattern: extract in dependency order (interfaces first, then knowledge base, then tools, then sub-agents, then prompts/middleware, then factory/server, finally the thin entry point), with characterization tests written before any code moves. This incremental order avoids the primary risk — breaking the working fault-diagnosis agent mid-refactoring with zero test coverage to catch regressions. The most critical LangChain-specific constraint is that `middleware` and `state_schema` are mutually exclusive in `create_agent()` (Issue #33217, closed "not planned"); the official workaround is to set `state_schema` as a class attribute on the middleware itself.

The main risks are non-technical: over-abstraction (building a framework for ten hypothetical projects when one real project exists), configuration explosion (inventing config options beyond the six hardcoded values that actually need externalizing), and import cycles from tools that reach back into `app.py` state. All three risks are addressed by a strict "nothing in `agent_core/` may import from `projects/`" dependency rule enforced before any file is moved. Security pre-work is also required: `app_copy.py` contains stale API keys and must be deleted before refactoring begins.

---

## Key Findings

### Recommended Stack

The stack is fixed — no version upgrades, no new framework-level dependencies. All patterns are achievable within the existing installed packages. The key insight is that LangChain 1.0 already provides the extension points needed: `create_agent()` accepts `middleware`, `context_schema`, and `checkpointer` as first-class parameters; `pydantic-settings` 2.x has native TOML support via `TomlConfigSettingsSource`; and `langchain_core.retrievers.BaseRetriever` is the correct abstraction for swappable knowledge bases (not a custom ABC). The patterns are explicit-over-magic throughout: dict-based registry (no `importlib.metadata` entry_points), explicit middleware list (no filesystem auto-discovery), TOML for values only (never for behavior like which tool classes to load).

See `.planning/research/STACK.md` for full implementation patterns and code samples.

**Core technologies:**
- Python 3.12 + LangChain 1.0.3: Agent creation, middleware, tools — fixed constraint, all patterns work within it
- LangGraph 1.0.5: Execution graph, SSE streaming, PostgreSQL checkpointing — do not upgrade `langgraph-checkpoint-postgres` (schema breaking change)
- FastAPI 0.121.0: HTTP/SSE server — frontend API contract must not change; all SSE event types must remain identical
- pydantic-settings 2.11.0: Config management via TOML + env vars — already installed, `TomlConfigSettingsSource` is built-in
- `langchain_core.retrievers.BaseRetriever`: RAG abstraction interface — subclass directly, no custom ABC needed

**Critical version constraint:** LangChain 1.0.3 — `middleware` + `state_schema` are mutually exclusive in `create_agent()`. Use `AgentMiddleware.state_schema` class attribute instead.

### Expected Features

The research identifies a clear 3-tier feature set for this refactoring milestone. All P1 features must land for the refactoring to be considered complete; P2 features are added when the core is stable; P3+ are deferred until real need emerges.

See `.planning/research/FEATURES.md` for prioritization matrix and dependency graph.

**Must have (table stakes — v1):**
- Tool registration interface — standardized `ToolRegistry` or `tools/__init__.py` convention the framework consumes
- Middleware list configuration — `create_agent(middleware=[...])` wired per-project, not hardcoded in lifespan
- Pydantic-validated configuration — typed settings loaded from `.env`/TOML with fail-fast startup errors
- Knowledge base abstraction — config-driven `FAISSKnowledgeBase(KnowledgeBase)` replacing hardcoded URLs
- System prompt isolation — project provides `get_system_prompt()` / `@dynamic_prompt`; framework never owns prompts
- Single-file thin wrapper entry — `create_app(tools, middleware, config)` returns a ready FastAPI app in ~40 lines
- Backward compatibility — existing fault-diagnosis agent passes smoke tests after refactoring

**Should have (v1.x — after core is stable):**
- Startup validation with actionable errors — health-check Ollama endpoint, DB connections at lifespan startup
- Typed `context_schema` per project — expose middleware `state_schema` extension point as documented pattern
- Structured logging — replace `print()` + emoji pattern with `logging`/`structlog` JSON format
- Knowledge base multi-index support — generalize single-index abstraction when a second project needs it

**Defer (v2+):**
- LLM Tool Selector middleware — only valuable when a project exceeds ~15 tools
- Sub-agent as tool convention — formalize when a second sub-agent scenario appears
- Project scaffolding CLI — only worth building after 2-3 real projects validate the skeleton

**Never build (anti-features):**
- Runtime hot-swapping of tools/middleware — Python reference model makes this unreliable in production
- YAML-driven agent definition — obscures LangChain APIs, breaks IDE tooling
- Auto-discovery of tools via filesystem scanning — non-deterministic import order, silent collisions
- Multi-tenant agent server (multiple projects in one process) — checkpointer and tool namespace conflicts

### Architecture Approach

The target architecture enforces a strict three-zone separation: `agent_core/` (framework, zero domain knowledge), `projects/fault_diagnosis/` (domain modules, zero framework code), and a thin `main.py` entry point that assembles both. The dependency flow is unidirectional: `projects/` imports from `agent_core/`, never the reverse. `agent_core/interfaces/` uses Python `typing.Protocol` (not ABC) for all extension points — domain implementations satisfy them structurally without forced inheritance. Resources with lifetimes tied to the application (DB connections, FAISS index) are initialized in FastAPI lifespan and passed to tools via closures, not module-level globals.

See `.planning/research/ARCHITECTURE.md` for the full directory layout, component responsibility table, and 7-step refactoring order.

**Major components:**
1. `agent_core/server/app_factory.py` — `create_app(config, tools, middleware, context_schema) -> FastAPI`; single assembly point
2. `agent_core/agent/factory.py` — `build_agent()` wrapper around `create_agent()`; lifespan orchestration; middleware assembly from config
3. `agent_core/tools/registry.py` — `ToolRegistry` with `@register_tool` decorator; `get_tools()` called after all imports
4. `agent_core/knowledge_base/` — `KnowledgeBaseProtocol` + `FAISSKnowledgeBase(config)` implementation
5. `agent_core/config/` — TOML/env loader + Pydantic schema (`ProjectConfig`, `KBConfig`, `MiddlewareConfig`)
6. `projects/fault_diagnosis/main.py` — ~40 lines: load config, import domain modules (side-effect registration), call `create_app()`
7. `projects/fault_diagnosis/tools/` — 5 files split by concern: sql, kb, report, viz, utility

### Critical Pitfalls

See `.planning/research/PITFALLS.md` for full details, recovery strategies, and phase-to-pitfall mapping.

1. **No tests before refactoring** — Write characterization tests capturing current SSE event sequence and API contract before moving any code. Every subsequent phase is gated on "smoke tests pass." This is the single highest-risk item.
2. **`middleware` + `state_schema` mutual exclusion** — In LangChain 1.0.3, passing both to `create_agent()` raises `AssertionError`. Always place custom state in `AgentMiddleware.state_schema` class attribute. Verify in a minimal script before extracting the agent factory.
3. **Module-level DB connections causing import-time failures** — `tools.py` creates `SQLDatabase` and `SQLDatabaseToolkit` at import time. Move all connection initialization to FastAPI lifespan; pass via closures. Tool modules must be importable in a test environment with no DB running.
4. **Import cycles from monolith split** — `fig_inter` in `app.py` uses `globals()` scoped to `app.py`; extracting it creates back-references to the entry point. Enforce the dependency hierarchy in writing before moving any file: nothing in `agent_core/` imports from `projects/`; nothing in `projects/` imports from `main.py`.
5. **Over-abstraction** — Only abstract what the fault-diagnosis project itself exercises. No ABC with a single subclass. No config keys not read by any current code. The "thin wrapper" entry point must require no more than 5-6 framework imports.
6. **Security pre-work** — `app_copy.py` contains stale hardcoded API keys. Delete it before any refactoring commit. Verify with `git log --all -S "sk-"`. Fix the SQL injection in `subagent/call_api_tool.py` during (not after) subagent modularization.

---

## Implications for Roadmap

Based on research, the refactoring maps cleanly to 7 ordered phases derived from the dependency structure in ARCHITECTURE.md. Each phase produces a runnable system; no phase leaves the codebase in a broken state.

### Phase 1: Safety Net — Characterization Tests + Pre-work
**Rationale:** Zero tests exist. Any code movement without a safety net risks silent regressions in the live agent. This phase must complete before a single file is moved. Also handles the security pre-work (delete `app_copy.py`, rotate keys) that would otherwise contaminate all subsequent commits.
**Delivers:** Smoke test suite (httpx AsyncClient against FastAPI TestClient) covering SSE event sequence, `/ai/history`, `/api/todos` endpoints. `app_copy.py` deleted. API key rotation confirmed.
**Addresses:** PITFALLS.md Pitfall 1 (no tests), security pre-work
**Avoids:** Silent regressions throughout all subsequent phases

### Phase 2: Project Structure + Interfaces + Config Schema
**Rationale:** Pure additions — nothing is moved yet. Establishes the directory skeleton and defines the Pydantic config schema, then creates `projects/fault_diagnosis/config.yaml` by externalizing hardcoded values. Tests the config loading path without breaking the running app. Defining interfaces before any extraction prevents import cycles.
**Delivers:** `agent_core/` directory skeleton, `agent_core/interfaces/` Protocol definitions, `agent_core/config/schema.py` Pydantic models, `projects/fault_diagnosis/config.yaml` with the 6 currently hardcoded values externalized
**Uses:** pydantic-settings 2.x `TomlConfigSettingsSource`, Python `typing.Protocol`
**Avoids:** PITFALLS.md Pitfall 4 (import cycles), Pitfall 6 (configuration explosion — only externalize values that exist today)

### Phase 3: Knowledge Base Extraction
**Rationale:** `knowledge_base.py` has no dependencies on `app.py` or `tools.py` — it is the cleanest extraction boundary. Completing it before tools eliminates the circular risk (`tools.py` imports `knowledge_base.py`; doing KB first means the target exists when tools are moved).
**Delivers:** `agent_core/knowledge_base/faiss_impl.py` (config-driven, no hardcoded URLs), closure-based `make_kb_tool()` factory, 8-second timeout preserved, smoke tests still pass
**Implements:** `KnowledgeBaseProtocol`, `FAISSKnowledgeBase(KBConfig)`
**Avoids:** PITFALLS.md integration gotcha: `allow_dangerous_deserialization=True` must remain explicit; Ollama health-check ping added to lifespan

### Phase 4: Domain Tools Modularization
**Rationale:** Largest single chunk (596-line `tools.py` split across 5 files). Must come after KB extraction (KB tool uses the closure pattern). Module-level DB connections must be moved to lifespan in this phase — acceptance criteria requires tool modules importable with no DB running.
**Delivers:** `projects/fault_diagnosis/tools/` with 5 concern-separated files; all module-level DB/LLM initialization moved to lifespan; `ToolRegistry` wired; `load_dotenv()` centralized to entry point only
**Addresses:** FEATURES.md P1 — Tool registration interface
**Avoids:** PITFALLS.md Pitfall 3 (import-time DB connections), Anti-Pattern 1 (global resource init at module load), Anti-Pattern 2 (monolithic tools.py)

### Phase 5: Sub-Agent + Prompts + Middleware Extraction
**Rationale:** Sub-agent is already partially isolated; this is mostly a rename + import fix. Prompts and middleware are extracted in the same phase because they reference each other (`identity_aware_prompt` imports from `prompts/`). The `import os` missing bug in the sub-agent is fixed here, not carried forward.
**Delivers:** `projects/fault_diagnosis/sub_agents/fault_explanation/` (with SQL injection fix), `projects/fault_diagnosis/prompts/`, `projects/fault_diagnosis/middleware/identity_prompt.py`
**Addresses:** FEATURES.md P1 — System prompt isolation
**Avoids:** PITFALLS.md — SQL injection carried forward into refactored subagent

### Phase 6: Agent Factory + Server (Core Framework)
**Rationale:** All domain modules are settled. The factory is written knowing exactly what it receives. This is the phase where the `middleware` + `state_schema` constraint is verified in a minimal script before the factory is extracted. SSE routes are moved from `app.py` to `agent_core/server/routes.py`.
**Delivers:** `agent_core/agent/factory.py`, `agent_core/server/app_factory.py`, `agent_core/server/routes.py`, `build_middleware_from_config()` assembly function, `MIDDLEWARE_REGISTRY` dict (no eval/importlib)
**Uses:** LangChain 1.0.3 `create_agent()`, `AgentMiddleware.state_schema` workaround
**Avoids:** PITFALLS.md Pitfall 2 (middleware + context_schema incompatibility), Anti-Pattern 3 (entry point owning business logic)

### Phase 7: Thin Entry Point + Integration Validation
**Rationale:** The thin `main.py` is the integration test of all previous phases — it proves the framework is genuinely reusable. `app.py` is deleted only after full validation. Frontend smoke test run against the refactored backend.
**Delivers:** `projects/fault_diagnosis/main.py` (~40 lines), `app.py` deleted (or renamed `app_legacy.py` until validated then deleted), full "Looks Done But Isn't" checklist verified, Vue frontend smoke test passing
**Addresses:** FEATURES.md P1 — Single-file thin wrapper entry, Backward compatibility
**Avoids:** PITFALLS.md — Frontend API contract broken; PostgreSQL checkpoint persistence regression

### Phase Ordering Rationale

- **Tests first, code second:** The absence of tests is the single biggest risk. No phase proceeds without smoke tests passing — this is non-negotiable given zero existing test coverage.
- **Pure additions before deletions:** Phases 2 and 3 add new files without removing old ones. The system is always in a runnable state.
- **Dependency-order extraction:** KB before Tools before Factory. This mirrors the actual import graph and prevents the circular import that would result from extracting the factory before the domain modules it depends on.
- **Security pre-work in Phase 1:** API keys in `app_copy.py` must not propagate into any new module. Delete before touching anything.
- **Abstraction bounded by Phase 2:** Config schema is written once against real hardcoded values. No expansion until a second project's config.yaml proves new keys are needed.

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 6 (Agent Factory):** The exact `create_agent()` parameter combination (middleware + context_schema + checkpointer) needs a verification script run against the actual installed LangChain 1.0.3 before the factory is written. The GitHub issue #33217 workaround is documented but behavior of edge cases (e.g., multiple middlewares each with their own `state_schema`) is not fully verified.
- **Phase 4 (Tools Modularization):** `fig_inter` uses `globals()` to share a mutable namespace across tool calls. The replacement pattern (local namespace dict passed via closure or `ToolContext` dataclass) needs a concrete design decision before implementation to avoid re-introducing cross-request state leaks.

Phases with standard patterns (skip research-phase):
- **Phase 1 (Tests):** `httpx.AsyncClient` + FastAPI `TestClient` is a well-documented pattern. No research needed.
- **Phase 2 (Structure + Config):** pydantic-settings TOML and Python Protocol are standard library features with official documentation. No research needed.
- **Phase 3 (KB Extraction):** `BaseRetriever` subclassing and closure injection are established LangChain patterns documented in STACK.md. No research needed.
- **Phase 5 (Prompts + Middleware):** `@dynamic_prompt` pattern is fully documented. No research needed.
- **Phase 7 (Integration):** Standard validation checklist, no new technology.

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Core patterns verified from LangChain GitHub source, official docs, and pydantic-settings docs. One MEDIUM area: exact `SummarizationMiddleware` parameter names should be re-checked if LangChain version is ever upgraded. |
| Features | HIGH | P1 feature set is driven by concrete gaps in the existing codebase (hardcoded values, monolithic tools.py), not speculation. Anti-feature list is well-reasoned with specific failure modes documented. |
| Architecture | HIGH | Directory structure and component boundaries derived from direct codebase analysis (`app.py`, `tools.py`, `knowledge_base.py`) plus verified LangChain 1.0 middleware architecture patterns. Refactoring order is proven by dependency graph, not preference. |
| Pitfalls | HIGH | All 6 critical pitfalls are grounded in: (a) direct observation of existing code issues from CONCERNS.md, (b) confirmed GitHub issues for LangChain-specific constraints, or (c) well-documented Python anti-patterns. No speculative pitfalls. |

**Overall confidence:** HIGH

### Gaps to Address

- **`fig_inter` globals() replacement:** The current `fig_inter` tool in `app.py` uses `globals()` to maintain a mutable execution namespace across Python code execution steps. The replacement pattern needs explicit design before Phase 4 begins. Options: (a) `ToolContext` dataclass passed at tool creation time, (b) thread-local storage, (c) session-scoped dict stored in LangGraph state. The choice has implications for test isolation and concurrent safety.
- **SummarizationMiddleware exact API:** Parameter names (`trigger`, `keep` vs `max_tokens_before_summary`, `messages_to_keep`) vary across LangChain 1.0.x patch releases. Verify against the actual installed version before implementing Phase 6.
- **AsyncPostgresSaver setup():** The lifespan currently calls `checkpointer.setup()`. After extracting the agent factory, verify that `setup()` is still called exactly once and before any agent invocation — not duplicated or skipped.
- **FAISS index rebuild concurrency:** `rebuild_kb.py` is an offline process; behavior when run while the server is running (lock contention on the FAISS index directory) should be explicitly tested or documented as unsupported during Phase 3.

---

## Sources

### Primary (HIGH confidence)
- LangChain GitHub Issue #33217 — `middleware` + `state_schema` mutual exclusion, official workaround confirmed
- LangChain AgentMiddleware source (GitHub) — complete hook signatures, execution order
- LangChain agents reference docs — `create_agent()` parameters, `context_schema`, middleware list
- pydantic-settings official docs — `TomlConfigSettingsSource`, `settings_customise_sources`
- FastAPI official docs — lifespan events, `app.state` singleton pattern
- Codebase direct analysis — `app.py`, `tools.py`, `knowledge_base.py`, `.planning/codebase/CONCERNS.md`
- LangGraph checkpoint issue #3557 — schema breaking change between `langgraph-checkpoint-postgres` versions
- LangGraph AsyncPostgresSaver issue #2755 — `autocommit=True` and `row_factory=dict_row` requirements

### Secondary (MEDIUM confidence)
- DeepWiki: LangChain Agent System with Middleware — hook execution order, `wrap_model_call` nesting
- LangChain middleware blog post — composition order, before/after hooks
- Shopify Engineering: Strangler Fig Pattern — incremental migration, characterization tests
- Modular monolith in Python (breadcrumbscollector.tech) — component structure, registry without entry_points
- FastAPI LangGraph production template (GitHub) — community reference for assembly patterns

### Tertiary (LOW confidence)
- AI Agent Anti-Patterns (Medium, Mar 2026) — over-abstraction patterns; content directionally consistent with primary sources
- Python hot-reloading misadventures (pierce.dev) — runtime hot-swap risks; validates anti-feature decision

---

*Research completed: 2026-03-26*
*Ready for roadmap: yes*
