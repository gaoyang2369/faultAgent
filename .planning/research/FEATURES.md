# Feature Research

**Domain:** Modular AI Agent Framework (LangChain/LangGraph-based, Python)
**Researched:** 2026-03-26
**Confidence:** HIGH (LangChain 1.0 middleware/tool APIs verified via official docs + Context7; plugin patterns verified via multiple sources)

---

## Project Context

This research targets a specific refactoring goal: splitting a 592-line monolithic `app.py` into a "shared core framework + pluggable modules" architecture. The framework must serve the existing fault-diagnosis project as its first consumer while enabling other domain projects to be built without touching framework code.

Constraints: LangChain 1.0.3 + LangGraph 1.0.5 + FastAPI 0.121.0 — no version upgrades. No new heavyweight dependencies. API contract with existing frontend must not break.

---

## Feature Landscape

### Table Stakes (Users Expect These)

Features a developer expects when onboarding a new project into "a modular agent framework." Missing these means the framework isn't actually modular — it's just reorganized code.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **Tool registration interface** | Developers define tools with `@tool` + Pydantic schema; framework assembles the list | LOW | Already exists via LangChain `@tool`; the gap is a standard convention for *collecting* tools into a project-level list, not inventing a new decorator |
| **Middleware list configuration** | `create_agent()` already accepts `middleware=[...]`; making this list configurable per project (not hardcoded in `lifespan`) is the expected extension point | LOW | LangChain 1.0 built-in middleware includes TodoList, Summarization, Retry, PII, HITL, etc. — these just need wiring |
| **Knowledge base abstraction** | Any project needs to swap embedding model, vector store path, and document source without editing framework files | MEDIUM | Current: Ollama endpoint + FAISS path hardcoded in `knowledge_base.py`; need a config-driven initializer |
| **Pydantic-validated configuration** | Framework settings (LLM endpoint, DB URLs, middleware params, KB paths) loaded from `.env` + optional YAML/TOML file, validated at startup | LOW | `pydantic-settings` already in ecosystem; `python-dotenv` already used; gap is adding structured validation with clear error messages |
| **Project directory convention** | Clear, documented folder layout for a new project: where tools go, where prompts go, where KB config goes | LOW | Not a library feature — a convention + example project; absence means every project improvises a structure |
| **System prompt isolation** | Each project defines its own system prompt template and `@dynamic_prompt` function without modifying framework files | LOW | Currently in `prompt_template.py` which is already a separate file — formalize this as "project must provide X" |
| **Single-file thin wrapper entry** | A new project should launch with one `main.py` (or `app.py`) that imports the framework and supplies project-specific objects; the framework handles FastAPI setup, agent creation, SSE streaming | MEDIUM | This is the most visible "table stakes" deliverable — proves the framework is genuinely reusable |
| **Backward compatibility for first consumer** | The existing fault-diagnosis project must run unchanged after refactoring | MEDIUM | Zero regression is table stakes when refactoring, not a feature; but it constrains every interface decision |

### Differentiators (Competitive Advantage)

Features that distinguish a well-designed framework from a bare module split. Not required for day-one usability, but provide serious leverage when building project 2, 3, and beyond.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Typed `context_schema` per project** | Each project declares its own context fields (e.g., `user_identity`, `equipment_type`) in a Pydantic model; framework merges these into the LangGraph state without boilerplate | MEDIUM | LangChain 1.0 supports `context_schema` in `create_agent()`; need to surface this cleanly as a project extension point. Caveat: middleware + state_schema are mutually exclusive in current LangChain — must use middleware's `state_schema` attribute instead |
| **LLM Tool Selector middleware** | Built-in LangChain middleware that uses a small LLM to pre-filter relevant tools before the main model sees them — enables projects with 20+ tools to stay fast | MEDIUM | Only valuable once a project has many tools; leverage existing `LLMToolSelectorMiddleware` rather than building custom logic |
| **Sub-agent as tool convention** | Documented pattern for wrapping a specialized agent as a LangChain `@tool`, enabling hierarchical agent composition without framework changes | LOW | Pattern already proven in the codebase (`fault_explanation_tool`); formalizing it as a documented convention is the value |
| **Knowledge base multi-index support** | A project can register multiple knowledge bases (e.g., equipment manuals + regulatory docs) with distinct retriever configurations, each exposed as a separate tool | MEDIUM | Current design has one global FAISS index; abstraction layer enables per-KB config but adds complexity |
| **Startup validation with actionable errors** | Framework verifies at startup: all required config keys present, DB connections reachable, KB index exists; fails fast with clear messages instead of cryptic runtime errors | LOW | Implemented as a startup health-check in `lifespan`; high value, low cost |
| **Built-in structured logging** | Replace `print()` + emoji-prefix pattern with `structlog` or `logging` + JSON format; structured logs enable log aggregation in production | LOW | Current codebase has zero structured logging; this is a one-time framework-level change that all projects inherit |

### Anti-Features (Commonly Requested, Often Problematic)

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| **Runtime hot-swapping of tools/middleware** | "Update a running agent without restarting the server" sounds appealing | Python's reference model makes `importlib.reload()` unreliable in production: memory bloat, stale references to old classes, inconsistent state in existing LangGraph checkpointer threads. Security risk if reload path is not tightly controlled. LangGraph's async worker state becomes undefined after mid-flight module changes | Deploy is fast (`gunicorn` graceful restart takes seconds); just restart. Design for restartability instead |
| **YAML-driven agent definition (no Python)** | "Configure the entire agent in YAML" reduces the need to write Python | For a developer tool, this adds a YAML-to-Python translation layer that obscures LangChain APIs, limits debuggability, and breaks IDE tooling. Every new LangChain feature requires framework YAML schema updates. PydanticAI has `Agent.from_file()` but even they recommend Python for non-trivial agents | Use YAML/TOML only for values (endpoints, paths, parameters), never for behavior (which tools, which middleware logic) |
| **Auto-discovery of tools via filesystem scanning** | "Drop a Python file in `/tools/` and it appears automatically" | Import order becomes non-deterministic, circular imports emerge, tools with name collisions silently overwrite each other, testing individual tools requires loading the whole project | Explicit list in `tools/__init__.py` (or equivalent). Explicit is better than implicit per Python's own philosophy |
| **Generic plugin marketplace / distribution via PyPI** | "Publish your tools as a pip package others can install" | Out of scope per PROJECT.md. Adds versioning, packaging, dependency conflict, and API stability concerns that dwarf the actual refactoring work | Keep framework as a local package (importable directory or internal namespace); revisit after multiple real projects validate the API |
| **Multi-tenant agent server (multiple projects in one process)** | "Run fault-diagnosis and a new project from a single FastAPI instance" | LangGraph uses a single global checkpointer; middleware state schemas must be merged at create-time; tool namespaces collide; lifespan management becomes a nightmare | One FastAPI process per project. Framework code is shared (library), not runtime-shared (service) |
| **GUI configuration panel** | "A web UI to manage tools and middleware at runtime" | This is a product on its own, not a framework feature. Runtime mutation of middleware pipelines reintroduces all the hot-swap problems above | Use a well-structured config file + restart |

---

## Feature Dependencies

```
[Single-file thin wrapper entry]
    └──requires──> [Tool registration interface]
    └──requires──> [Middleware list configuration]
    └──requires──> [Pydantic-validated configuration]
    └──requires──> [System prompt isolation]
    └──requires──> [Knowledge base abstraction]

[Knowledge base abstraction]
    └──requires──> [Pydantic-validated configuration]
                       (KB paths, embedding endpoints come from config)

[Typed context_schema per project]
    └──requires──> [Middleware list configuration]
                       (LangChain constraint: state extension must go via middleware's
                        state_schema, not create_agent(state_schema=...) when middleware is used)

[LLM Tool Selector middleware]
    └──requires──> [Tool registration interface]
                       (needs a pool of tools to select from)
    └──enhances──> [Typed context_schema per project]
                       (selector can use context fields to pre-filter)

[Sub-agent as tool convention]
    └──requires──> [Tool registration interface]
                       (sub-agent is registered as a regular @tool)

[Knowledge base multi-index support]
    └──requires──> [Knowledge base abstraction]
                       (single-index abstraction must be generalized first)

[Startup validation]
    └──requires──> [Pydantic-validated configuration]
                       (validates config fields before checking external resources)

[Structured logging]
    └──no dependencies──> (standalone, implement early)

[Runtime hot-swapping] ──conflicts──> [LangGraph checkpointer state]
[YAML-driven agent definition] ──conflicts──> [IDE tooling / debuggability]
[Auto-discovery] ──conflicts──> [Deterministic tool ordering]
[Multi-tenant server] ──conflicts──> [Single checkpointer assumption]
```

### Dependency Notes

- **Thin wrapper requires everything else:** The wrapper entry point is the integration test of all other features; implement it last as a validation that the framework is genuinely usable.
- **context_schema via middleware, not create_agent:** LangChain 1.0.3 has a known constraint — `middleware` and `state_schema` are mutually exclusive in `create_agent()`. Custom context fields must be added via each middleware's own `state_schema` attribute. This affects how the framework exposes the `context_schema` extension point.
- **Knowledge base multi-index is a v1.x feature:** The abstraction must support single-index first; generalization to multi-index follows naturally but should not block v1.

---

## MVP Definition

The goal is a working framework with the fault-diagnosis project as its first consumer. "Working" means: another developer could follow a README and build a new domain agent without reading framework internals.

### Launch With (v1)

- [ ] **Tool registration interface** — standardized `tools/__init__.py` convention exporting a `TOOLS: list` that the framework consumes. No new decorator needed; convention over magic.
- [ ] **Middleware list configuration** — framework accepts a `middleware: list[AgentMiddleware]` from the project's entry point; built-in middleware (TodoList, Summarization) are importable from framework but optional per project.
- [ ] **Pydantic-validated configuration** — `FrameworkSettings(BaseSettings)` loads from `.env` with typed fields for LLM endpoint, DB URLs, model name; project extends with its own `ProjectSettings`.
- [ ] **Knowledge base abstraction** — `KnowledgeBaseConfig` dataclass with fields for embedding endpoint, model, index path, chunk size; framework provides `build_retriever(config)` factory; hardcoded values eliminated.
- [ ] **System prompt isolation** — framework defines the prompt interface (`get_system_prompt(context) -> str`); project implements it; no prompt logic inside framework files.
- [ ] **Single-file thin wrapper entry** — a new project's `main.py` imports `create_framework_app(tools, middleware, kb_config, get_system_prompt, settings)` and gets a ready FastAPI app back.
- [ ] **Backward compatibility** — existing fault-diagnosis project passes all its current integration tests after refactoring.

### Add After Validation (v1.x)

- [ ] **Startup validation with actionable errors** — add after v1 is running in production; the framework structure must be stable before investing in diagnostics.
- [ ] **Typed context_schema per project** — expose middleware `state_schema` extension point as a documented pattern once the base middleware layer is proven stable.
- [ ] **Structured logging** — replace print/emoji pattern framework-wide; add after core refactoring is merged to avoid merge conflicts.
- [ ] **Knowledge base multi-index support** — add when the second real project needs more than one KB.

### Future Consideration (v2+)

- [ ] **LLM Tool Selector middleware** — only valuable when a project exceeds ~15 tools; defer until that need materializes.
- [ ] **Sub-agent as tool convention** — already proven in codebase; formalize as documented pattern in v2 when a second sub-agent scenario appears.
- [ ] **Project scaffolding CLI** — `python -m framework new my-project` generates directory skeleton; only worth building after 2-3 real projects have validated what the skeleton should contain.

---

## Feature Prioritization Matrix

| Feature | Developer Value | Implementation Cost | Priority |
|---------|----------------|---------------------|----------|
| Tool registration interface | HIGH | LOW | P1 |
| Pydantic-validated configuration | HIGH | LOW | P1 |
| Single-file thin wrapper entry | HIGH | MEDIUM | P1 |
| Middleware list configuration | HIGH | LOW | P1 |
| System prompt isolation | HIGH | LOW | P1 |
| Knowledge base abstraction | HIGH | MEDIUM | P1 |
| Backward compatibility | HIGH | MEDIUM | P1 |
| Startup validation | MEDIUM | LOW | P2 |
| Typed context_schema per project | MEDIUM | MEDIUM | P2 |
| Structured logging | MEDIUM | LOW | P2 |
| KB multi-index support | MEDIUM | MEDIUM | P2 |
| LLM Tool Selector middleware | LOW | LOW | P3 |
| Sub-agent convention (documented) | LOW | LOW | P3 |
| Project scaffolding CLI | LOW | MEDIUM | P3 |
| Runtime hot-swapping | LOW | HIGH | NEVER |
| YAML-driven agent definition | LOW | HIGH | NEVER |
| Auto-discovery via filesystem | LOW | MEDIUM | NEVER |

**Priority key:**
- P1: Must have for launch — refactoring milestone is incomplete without these
- P2: Should have, add when core is stable
- P3: Nice to have, tackle when real need emerges
- NEVER: Anti-features — explicitly avoid

---

## Framework Ecosystem Comparison

How the major modular agent patterns compare, contextualized for this project's constraints.

| Pattern | Used By | Tool Registration | Middleware | Config | Fit for This Project |
|---------|---------|-------------------|------------|--------|----------------------|
| **LangChain 1.0 create_agent + middleware list** | This project (existing) | `@tool` list passed at creation | Composable list, 16 built-ins | Python / .env | BEST — already in use, no new deps |
| **LangGraph-bigtool (semantic tool registry)** | Large-tool-count agents | UUID-keyed dict + semantic search | N/A (separate concern) | Python | Overkill — relevant only if tool count exceeds ~20 |
| **Semantic Kernel (Microsoft)** | C#/Python enterprise | Plugin classes with kernel registration | Pipeline-based | YAML + code | Wrong stack — adds heavy dep, different paradigm |
| **FastAPI + dependency injection pattern** | FastAPI ecosystem | DI via `Depends()` | Starlette middleware | Pydantic Settings | Useful only at the API layer, not agent layer |
| **Cookiecutter/Copier project template** | Multi-project frameworks | N/A (scaffold only) | N/A | N/A | Useful for project scaffolding (v2+), not for runtime |

**Recommendation for this project:** Stay entirely within LangChain 1.0 `create_agent()` + middleware composition. Do not introduce a plugin registry abstraction — the tool list passed to `create_agent()` IS the registry. Configuration via `pydantic-settings` + `.env`. No YAML for behavior.

---

## Sources

- [LangChain Prebuilt Middleware — Official Docs](https://docs.langchain.com/oss/python/langchain/middleware/built-in) — HIGH confidence, official
- [Agent Creation and Middleware Architecture — DeepWiki](https://deepwiki.com/langchain-ai/langchain/4.1-agent-system-with-middleware) — HIGH confidence, current docs mirror
- [LangGraph-bigtool Tool Registry — GitHub](https://github.com/langchain-ai/langgraph-bigtool) — HIGH confidence, official LangChain repo
- [LangChain 1.0 Middleware Study Guide — Colin McNamara](https://colinmcnamara.com/blog/langchain-middleware-study-guide) — MEDIUM confidence, community
- [middleware + state_schema mutual exclusion — GitHub Issue #33217](https://github.com/langchain-ai/langchain/issues/33217) — HIGH confidence, verified bug report
- [Python Plugin Architecture — Python Packaging User Guide](https://packaging.python.org/en/latest/guides/creating-and-discovering-plugins/) — HIGH confidence, official
- [Registry Pattern with Decorators — Medium, Dec 2025](https://medium.com/@tihomir.manushev/implementing-the-registry-pattern-with-decorators-in-python-de8daf4a452a) — MEDIUM confidence
- [Pydantic Settings — Official Docs](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) — HIGH confidence, official
- [FastAPI LangGraph Production Template — GitHub](https://github.com/wassim249/fastapi-langgraph-agent-production-ready-template) — MEDIUM confidence, community reference
- [Python Hot Reloading Misadventures — pierce.dev](https://pierce.dev/notes/misadventures-in-python-hot-reloading/) — MEDIUM confidence, practitioner experience
- [AI Agent Anti-Patterns — Medium, Mar 2026](https://achan2013.medium.com/ai-agent-anti-patterns-part-1-architectural-pitfalls-that-break-enterprise-agents-before-they-32d211dded43) — MEDIUM confidence

---

*Feature research for: Modular AI Agent Framework (LangChain/LangGraph + FastAPI)*
*Researched: 2026-03-26*
