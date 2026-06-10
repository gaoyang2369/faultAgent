# Architecture Research

**Domain:** Modular Python AI Agent Framework (LangChain 1.0 / LangGraph 1.0 on FastAPI)
**Researched:** 2026-03-26
**Confidence:** HIGH (based on current codebase analysis + LangChain 1.0 official deepwiki)

---

## Standard Architecture

### System Overview

The target architecture separates the codebase into three distinct zones:

```
┌─────────────────────────────────────────────────────────────────────┐
│                     ENTRY POINT (thin)                               │
│   projects/fault_diagnosis/main.py  ←→  config.yaml                 │
│   (assembles core + domain modules, starts FastAPI)                  │
└─────────────────┬───────────────────────────────────────────────────┘
                  │ uses
┌─────────────────▼───────────────────────────────────────────────────┐
│                 CORE FRAMEWORK  (agent_core/)                        │
│  ┌──────────┐ ┌───────────┐ ┌─────────────┐ ┌───────────────────┐  │
│  │ server/  │ │ agent/    │ │ middleware/ │ │ knowledge_base/   │  │
│  │ FastAPI  │ │ factory   │ │ built-ins  │ │ base + FAISS impl │  │
│  │ routes   │ │ lifespan  │ │ protocol   │ │ protocol          │  │
│  └──────────┘ └───────────┘ └─────────────┘ └───────────────────┘  │
│  ┌──────────┐ ┌───────────┐ ┌─────────────────────────────────────┐ │
│  │ tools/   │ │ config/   │ │ interfaces/ (Python Protocols)      │ │
│  │ protocol │ │ loader +  │ │  ToolProtocol, MiddlewareProtocol,  │ │
│  │ registry │ │ Pydantic  │ │  KnowledgeBaseProtocol              │ │
│  └──────────┘ └───────────┘ └─────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
                  │ imports from
┌─────────────────▼───────────────────────────────────────────────────┐
│              DOMAIN MODULES  (projects/fault_diagnosis/)             │
│  ┌──────────┐ ┌───────────┐ ┌──────────────┐ ┌──────────────────┐  │
│  │ tools/   │ │ prompts/  │ │ middleware/  │ │ knowledge_base/  │  │
│  │ sql_tool │ │ system    │ │ identity_    │ │ pdfs/, faiss_db/ │  │
│  │ kb_tool  │ │ prompt    │ │ aware_prompt │ │ config           │  │
│  │ report   │ │ templates │ │             │ │                  │  │
│  └──────────┘ └───────────┘ └──────────────┘ └──────────────────┘  │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ sub_agents/ (fault_explanation, etc.)                        │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | What It Must NOT Do |
|-----------|----------------|---------------------|
| `agent_core/agent/factory.py` | `create_agent()` wrapper; lifespan orchestration; assembles tools + middleware + checkpointer | Know anything about fault diagnosis domain |
| `agent_core/server/routes.py` | SSE stream endpoint, history, todos, CORS | Own business logic; import domain tools directly |
| `agent_core/server/app_factory.py` | `create_app(config)` factory; lifespan registration; static mounts | Hard-code any path or credential |
| `agent_core/interfaces/` | Python `Protocol` definitions for Tool, Middleware, KnowledgeBase | Implementation code |
| `agent_core/tools/registry.py` | Decorator-based tool registry; assembles final `tools` list | Domain-specific logic |
| `agent_core/middleware/` | Built-in middleware (`TodoListMiddleware`, `SummarizationMiddleware`) wrappers; `@dynamic_prompt` base | Project-specific prompts |
| `agent_core/knowledge_base/` | `KnowledgeBase` Protocol + FAISS implementation; rebuild CLI | Domain PDF paths or embedding URLs |
| `agent_core/config/` | YAML loader + Pydantic-validated settings models | Business logic |
| `projects/fault_diagnosis/tools/` | All `@tool` functions for this domain | Framework plumbing |
| `projects/fault_diagnosis/prompts/` | System prompt strings, `get_identity_system_prompt()` | HTTP routing |
| `projects/fault_diagnosis/middleware/` | `@dynamic_prompt` for identity-aware prompts | Generic middleware logic |
| `projects/fault_diagnosis/main.py` | 30-50 lines: loads config, calls `create_app()`, passes domain modules | Any logic beyond assembly |

---

## Recommended Project Structure

```
fault-diagnosis/
│
├── agent_core/                         # Shared framework — no domain knowledge
│   ├── __init__.py
│   ├── interfaces/
│   │   ├── __init__.py
│   │   ├── tool.py                     # ToolProtocol (typing.Protocol)
│   │   ├── middleware.py               # MiddlewareProtocol
│   │   └── knowledge_base.py          # KnowledgeBaseProtocol
│   ├── agent/
│   │   ├── __init__.py
│   │   └── factory.py                  # build_agent(config, tools, middleware, checkpointer)
│   ├── server/
│   │   ├── __init__.py
│   │   ├── app_factory.py             # create_app(config) → FastAPI
│   │   ├── routes.py                  # /chat/stream, /history, /todos
│   │   └── streaming.py              # token_stream_events() generator
│   ├── tools/
│   │   ├── __init__.py
│   │   └── registry.py                # @register_tool decorator + get_tools()
│   ├── middleware/
│   │   ├── __init__.py
│   │   └── builtin.py                 # Re-exports TodoListMiddleware, SummarizationMiddleware
│   ├── knowledge_base/
│   │   ├── __init__.py
│   │   ├── base.py                    # KnowledgeBase Protocol + abstract factory
│   │   └── faiss_impl.py             # FAISSKnowledgeBase(config) implementation
│   └── config/
│       ├── __init__.py
│       ├── loader.py                  # load_config(path) → ProjectConfig
│       └── schema.py                  # Pydantic models: ProjectConfig, KBConfig, MiddlewareConfig
│
├── projects/
│   └── fault_diagnosis/               # Domain-specific code for this deployment
│       ├── config.yaml                # All runtime config (no secrets — those stay in .env)
│       ├── main.py                    # Entry point: ~40 lines, pure assembly
│       ├── tools/
│       │   ├── __init__.py
│       │   ├── sql_tools.py           # sql_inter, extract_data (MySQL DCMA tools)
│       │   ├── knowledge_base_tool.py # query_knowledge_base (wraps KB instance)
│       │   ├── report_tools.py       # save_report, save_html_report
│       │   ├── viz_tools.py          # fig_inter, python_inter
│       │   └── utility_tools.py      # get_time, sanitize_for_json, etc.
│       ├── prompts/
│       │   ├── __init__.py
│       │   ├── system_prompt.py       # systemprompt (base string)
│       │   └── identity_prompts.py    # get_identity_system_prompt()
│       ├── middleware/
│       │   ├── __init__.py
│       │   └── identity_prompt.py     # @dynamic_prompt identity_aware_prompt + Context dataclass
│       ├── knowledge_base/
│       │   ├── pdfs/                  # PDF source documents
│       │   └── faiss_db/              # Generated FAISS index (gitignored)
│       └── sub_agents/
│           ├── __init__.py
│           └── fault_explanation/
│               ├── agent.py           # create_fault_explanation_agent()
│               ├── tools.py           # call_api_tool, fig_inter (sub-agent scope)
│               └── prompt.py         # FAULT_EXPLANATION_SYSTEM_PROMPT
│
├── agent_fronted/                     # Vue 3 frontend (unchanged)
│   └── ...
│
├── html_template.html                 # Report template (move to projects/fault_diagnosis/)
├── .env                               # Secrets only — never committed
├── requirements.txt
└── rebuild_kb.py                      # CLI: python rebuild_kb.py → delegates to agent_core/knowledge_base/
```

### Structure Rationale

- **`agent_core/`:** Zero domain knowledge. Any new project ignores this and writes only `projects/new_project/`. The framework evolves here without touching domain code.
- **`agent_core/interfaces/`:** Python `Protocol` classes (structural typing, no forced inheritance). Domain tools/middleware work without importing from `agent_core` if they satisfy the Protocol structurally.
- **`projects/fault_diagnosis/`:** Everything domain-specific. The entirety of the old `app.py` business logic, `tools.py` tool functions, `prompt_template.py`, and `knowledge_base.py` config ends up here.
- **`config.yaml` per project:** Declares what middleware to enable, KB paths, model names. Secrets (keys, passwords) remain in `.env`. The config file is committed; `.env` is not.
- **`projects/fault_diagnosis/main.py`:** The only file that knows about both `agent_core` and the domain modules. It loads config, instantiates domain components, and hands them to `create_app()`.

---

## Architectural Patterns

### Pattern 1: Registry with Decorator (Tool Assembly)

**What:** Tools register themselves via a `@register_tool` decorator in `agent_core/tools/registry.py`. Each project calls `get_tools()` to retrieve its assembled list.

**When to use:** When tools are defined across multiple files and the entry point should not enumerate them manually.

**Trade-offs:** Simple and zero-dependency. No auto-discovery magic — tools must explicitly call `@register_tool`. This is intentional: explicit beats implicit for debugging.

**Example:**

```python
# agent_core/tools/registry.py
from langchain_core.tools import BaseTool
from typing import List

_tool_registry: List[BaseTool] = []

def register_tool(tool):
    """Decorator: register a @tool-decorated function into the registry."""
    _tool_registry.append(tool)
    return tool

def get_tools() -> List[BaseTool]:
    return list(_tool_registry)

def clear_tools():
    """For testing: reset registry between test runs."""
    _tool_registry.clear()
```

```python
# projects/fault_diagnosis/tools/sql_tools.py
from agent_core.tools.registry import register_tool
from langchain_core.tools import tool

@register_tool
@tool(args_schema=SqlInterSchema)
def sql_inter(sql_query: str) -> str:
    ...
```

```python
# projects/fault_diagnosis/main.py
import projects.fault_diagnosis.tools  # side-effect: registers all tools
from agent_core.tools.registry import get_tools
tools = get_tools()
```

### Pattern 2: Application Factory with Typed Config (Entry Point)

**What:** `create_app(config: ProjectConfig) -> FastAPI` is the single assembly point. The entry point (`main.py`) loads config, imports domain modules (triggering registration), then calls the factory.

**When to use:** Always. Thin entry points enable testing without spinning up a live server.

**Trade-offs:** Requires a well-defined `ProjectConfig` schema. Adding new config keys requires updating the Pydantic model — this is the cost paid for type safety and fail-fast startup.

**Example:**

```python
# agent_core/server/app_factory.py
from agent_core.config.schema import ProjectConfig
from fastapi import FastAPI

def create_app(config: ProjectConfig, tools, middleware, context_schema) -> FastAPI:
    app = FastAPI(title=config.title, lifespan=make_lifespan(config, tools, middleware, context_schema))
    app.add_middleware(CORSMiddleware, allow_origins=config.cors_origins, ...)
    app.include_router(chat_router)
    _mount_static(app, config)
    return app
```

```python
# projects/fault_diagnosis/main.py  (~40 lines)
from agent_core.config.loader import load_config
from agent_core.server.app_factory import create_app
import projects.fault_diagnosis.tools          # registers tools
import projects.fault_diagnosis.middleware      # registers middleware
from agent_core.tools.registry import get_tools
from projects.fault_diagnosis.middleware.identity_prompt import identity_aware_prompt, Context
from langchain.agents.middleware import TodoListMiddleware, SummarizationMiddleware

config = load_config("projects/fault_diagnosis/config.yaml")

app = create_app(
    config=config,
    tools=get_tools(),
    middleware=[TodoListMiddleware(), identity_aware_prompt, SummarizationMiddleware(...)],
    context_schema=Context,
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

### Pattern 3: Protocol-Based Interfaces (Pluggability Without Coupling)

**What:** Use `typing.Protocol` (not ABC) for all extension points. Components in `agent_core` depend on the Protocol; domain implementations satisfy it structurally without importing from `agent_core`.

**When to use:** For KnowledgeBase, custom middleware, and any future extension points.

**Trade-offs:** No forced inheritance means easier to satisfy from external libraries. Slight loss of IDE "implement interface" tooling versus ABC, but `runtime_checkable` recovers most of this.

**Example:**

```python
# agent_core/interfaces/knowledge_base.py
from typing import Protocol, runtime_checkable, List
from langchain_core.documents import Document

@runtime_checkable
class KnowledgeBaseProtocol(Protocol):
    def invoke(self, query: str) -> List[Document]: ...
    def rebuild(self) -> None: ...
```

```python
# agent_core/knowledge_base/faiss_impl.py
from agent_core.config.schema import KBConfig

class FAISSKnowledgeBase:
    """Satisfies KnowledgeBaseProtocol structurally."""
    def __init__(self, config: KBConfig): ...
    def invoke(self, query: str) -> List[Document]: ...
    def rebuild(self) -> None: ...
```

```python
# projects/fault_diagnosis/config.yaml
knowledge_base:
  type: faiss
  pdf_dir: projects/fault_diagnosis/knowledge_base/pdfs
  index_dir: projects/fault_diagnosis/knowledge_base/faiss_db
  embedding_model: qwen3-embedding:8b
  embedding_base_url: http://10.108.13.254:11434
  top_k: 3
  timeout_seconds: 8
```

### Pattern 4: Closure-Based Tool Injection (Sharing Resources)

**What:** Tools that need shared resources (DB connections, KB retriever, LLM clients) receive them via closures at assembly time, not via global variables or module-level initialization.

**When to use:** For `query_knowledge_base` (needs KB retriever), `sql_inter` (needs DB connection), `fault_explanation_tool` (needs sub-agent factory).

**Trade-offs:** Resources initialized once in lifespan; passed into tool factory functions. Avoids global state in `tools.py`. Slightly more verbose at assembly time.

**Example:**

```python
# projects/fault_diagnosis/tools/knowledge_base_tool.py
from langchain_core.tools import tool
from agent_core.tools.registry import register_tool

def make_kb_tool(kb_retriever):
    """Factory: captures kb_retriever in closure, returns registered tool."""

    @register_tool
    @tool(args_schema=KBQuerySchema)
    def query_knowledge_base(query: str) -> str:
        """Query local knowledge base for equipment fault metadata."""
        ...use kb_retriever...

    return query_knowledge_base
```

```python
# projects/fault_diagnosis/main.py
from agent_core.knowledge_base.faiss_impl import FAISSKnowledgeBase
from projects.fault_diagnosis.tools.knowledge_base_tool import make_kb_tool

config = load_config(...)
kb = FAISSKnowledgeBase(config.knowledge_base)
make_kb_tool(kb)   # registers into registry as side-effect
```

Note: For LangChain 1.0, the `RuntimeContext` mechanism for injecting values during invocation (via `request.runtime.context`) already handles per-request injection (as used by `identity_aware_prompt`). The closure pattern is for initialization-time resources that are fixed for the lifetime of the process.

### Pattern 5: Config-Driven Middleware Assembly

**What:** The `config.yaml` declares which built-in middleware to enable and with what parameters. Custom project middleware is added explicitly in `main.py`. No reflection or dynamic loading.

**When to use:** For the standardized built-in middleware (TodoList, Summarization). Custom middleware is always explicit code, never auto-discovered from config.

**Example:**

```yaml
# projects/fault_diagnosis/config.yaml
middleware:
  todo_list:
    enabled: true
  summarization:
    enabled: true
    max_tokens_before_summary: 64000
    messages_to_keep: 20
  context_schema: projects.fault_diagnosis.middleware.identity_prompt.Context
```

```python
# agent_core/agent/factory.py
def build_middleware_from_config(config: MiddlewareConfig, extra_middleware: list) -> list:
    result = []
    if config.todo_list.enabled:
        result.append(TodoListMiddleware())
    result.extend(extra_middleware)   # project-specific come after built-ins
    if config.summarization.enabled:
        result.append(SummarizationMiddleware(
            model=summary_model,
            max_tokens_before_summary=config.summarization.max_tokens_before_summary,
            messages_to_keep=config.summarization.messages_to_keep,
        ))
    return result
```

---

## Data Flow

### Agent Assembly Flow (Startup)

```
main.py loads config.yaml
    ↓
FAISSKnowledgeBase(config.kb) initialized
    ↓
make_kb_tool(kb) → registers tool into registry
import sql_tools, report_tools, viz_tools → side-effect registrations
    ↓
get_tools() → assembled tool list
    ↓
build_middleware_from_config(config.middleware, [identity_aware_prompt]) → middleware list
    ↓
create_app(config, tools, middleware, Context)
    ↓
FastAPI lifespan: AsyncConnectionPool → AsyncPostgresSaver → create_agent()
    ↓
app.state.agent = agent  (singleton for process lifetime)
```

### Request Flow (SSE Chat)

```
GET /chat/stream?message=...&thread_id=...&user_identity=...
    ↓
routes.py: stream_chat_log_get() → StreamingResponse(token_stream_events())
    ↓
token_stream_events(): inject Context(user_identity=...) into runtime config
    ↓
app.state.agent.astream_events(inputs, config)
    ↓
LangGraph middleware chain:
  TodoListMiddleware.before_model() → identity_aware_prompt.modify_model_request()
  → SummarizationMiddleware.before_model()
    ↓
LLM → tool calls → ToolNode → LLM → ...
    ↓
SSE events: start | tool_start | tool_end | token | complete
    ↓
Frontend EventSource receives events reactively
```

### Knowledge Base Query Flow

```
Agent invokes query_knowledge_base tool
    ↓
Closure captures kb_retriever (FAISSKnowledgeBase instance)
    ↓
ThreadPoolExecutor (8s timeout) → kb_retriever.invoke(query)
    ↓
FAISS similarity search → top_k documents
    ↓
Returns formatted strings to agent
```

---

## Refactoring Order

Refactor in dependency order — each step produces a runnable system.

### Step 1: Extract Interfaces and Config Schema (no behavior change)

Create `agent_core/interfaces/` with Protocol definitions. Create `agent_core/config/schema.py` with Pydantic models. Create `projects/fault_diagnosis/config.yaml` (move hardcoded values out of Python files).

**Why first:** Pure additions. Nothing is moved yet. Tests the config loading path without breaking the running app.

**Dependency:** None.

### Step 2: Extract Knowledge Base (isolated module, easy boundary)

Move `knowledge_base.py` logic to `agent_core/knowledge_base/faiss_impl.py`. Update to read config from `ProjectConfig` instead of hardcoded URLs. Update `query_knowledge_base` tool to use the closure pattern.

**Why second:** `knowledge_base.py` has no dependencies on `app.py` or `tools.py` (it's imported by tools, not the reverse). Clean extraction.

**Dependency:** Step 1 (needs KBConfig schema).

### Step 3: Extract Domain Tools (largest single chunk)

Split `tools.py` (596 lines, 7 tools) across `projects/fault_diagnosis/tools/`:
- `sql_tools.py` — sql_inter + DCMA SQLDatabaseToolkit
- `knowledge_base_tool.py` — query_knowledge_base (closure pattern)
- `report_tools.py` — save_report, save_html_report
- `viz_tools.py` — fig_inter (move from app.py), python_inter
- `utility_tools.py` — get_time, sanitize_for_json, safe_json_dumps, parse_todos_from_tool_output

Move `tools` list assembly into `main.py` (via `get_tools()`).

**Why third:** Tools have circular risk — `tools.py` imports from `knowledge_base.py` (done in Step 2), and `app.py` imports from `tools.py`. Sequencing eliminates the circular hazard.

**Dependency:** Step 2. `app.py` still works during this step as `tools` import switches incrementally.

### Step 4: Extract Sub-Agent (contained directory, already isolated)

Move `subagent/` to `projects/fault_diagnosis/sub_agents/fault_explanation/`. Update imports. The `fault_explanation_tool` wrapper stays in `projects/fault_diagnosis/tools/`.

**Why fourth:** Sub-agent is already partially isolated in its own directory. This is a rename + import fix rather than a logic change.

**Dependency:** Step 3.

### Step 5: Extract Prompts and Middleware

Move `prompt_template.py` to `projects/fault_diagnosis/prompts/`. Move `identity_aware_prompt` + `Context` dataclass from `app.py` to `projects/fault_diagnosis/middleware/identity_prompt.py`.

**Why fifth:** Prompts and middleware depend on the tools being settled (Step 3) to know what to document. The `dynamic_prompt` depends on `ModelRequest` from LangChain — no internal dependencies.

**Dependency:** Step 3 (middleware references prompt imports).

### Step 6: Extract Agent Factory and Server (core framework)

Create `agent_core/agent/factory.py` with `build_agent()`. Create `agent_core/server/app_factory.py` with `create_app()`. Create `agent_core/server/routes.py` with the SSE route, history, and todos endpoints extracted from `app.py`. Create `agent_core/tools/registry.py`.

**Why sixth:** All domain modules are settled by now. The factory can be written knowing what it receives.

**Dependency:** Steps 1-5.

### Step 7: Write Thin Entry Point and Delete app.py

Create `projects/fault_diagnosis/main.py` (40-50 lines). Verify full system runs. Delete `app.py` (or keep temporarily as `app_legacy.py` until validated).

**Dependency:** Step 6.

---

## Component Boundaries (What Talks to What)

```
agent_core/interfaces/        ← imported by agent_core/* and projects/*
agent_core/config/            ← imported by agent_core/* and main.py only
agent_core/knowledge_base/    ← imported by agent_core/agent/factory.py and main.py
agent_core/tools/registry     ← imported by projects/*/tools/* (write) and main.py (read)
agent_core/agent/factory      ← imported by agent_core/server/app_factory.py only
agent_core/server/            ← imported by main.py only

projects/fault_diagnosis/tools/*      → imports from agent_core/tools/registry (register_tool)
projects/fault_diagnosis/middleware/* → imports from LangChain directly (dynamic_prompt, ModelRequest)
projects/fault_diagnosis/main.py      → imports from agent_core/server/app_factory + all project modules
```

**Hard rule:** Nothing inside `agent_core/` may import from `projects/`. The dependency flows one way only.

---

## Integration Points

### External Services

| Service | Location After Refactor | Config Source |
|---------|------------------------|---------------|
| OpenAI-compatible LLM API | `agent_core/agent/factory.py` builds `ChatOpenAI` | `config.yaml` → `ProjectConfig.model` |
| Ollama Embeddings | `agent_core/knowledge_base/faiss_impl.py` | `config.yaml` → `KBConfig.embedding_*` |
| MySQL (DCMA) | `projects/fault_diagnosis/tools/sql_tools.py` | `.env` (HOST, USER, MYSQL_PW, PORT) |
| PostgreSQL Checkpointer | `agent_core/agent/factory.py` lifespan | `.env` (POSTGRES_*) |
| External ML API (SHAP) | `projects/fault_diagnosis/sub_agents/fault_explanation/tools.py` | `.env` or `config.yaml` |
| Tavily Search | `projects/fault_diagnosis/tools/` | `.env` (TAVILY_API_KEY) |

### Internal Boundaries

| Boundary | Communication Pattern | Notes |
|----------|-----------------------|-------|
| `main.py` ↔ `agent_core/server/app_factory` | Function call: `create_app(config, tools, middleware, context_schema)` | Single assembly point |
| `projects/tools/*` ↔ `agent_core/tools/registry` | Decorator side-effect at import time | Import order matters: import tools before calling `get_tools()` |
| `agent_core/server/routes` ↔ `agent_core/agent` | `request.app.state.agent` (FastAPI state singleton) | Agent created once in lifespan |
| `tools/*` ↔ `KnowledgeBase` | Closure capturing KB instance at startup | No global `db_retriever` module variable |
| `middleware/identity_prompt` ↔ `prompts/` | Direct import within same project namespace | No cross-project imports |

---

## Anti-Patterns

### Anti-Pattern 1: Global Resource Initialization at Module Load

**What people do:** Current `tools.py` creates `SQLDatabase`, `ChatOpenAI`, and `TavilySearch` at module import time (lines 27-48). `knowledge_base.py` has a global `db_retriever = None`.

**Why it's wrong:** Initialization order becomes fragile. Tests fail because importing the module triggers DB connections. Error messages appear at import rather than at startup with context.

**Do this instead:** Initialize all resources inside the `lifespan` async context manager. Pass them to tool factories (closure pattern). The registry assembles the tool list after resources are ready.

### Anti-Pattern 2: Monolithic tools.py with Mixed Concerns

**What people do:** Current `tools.py` contains tool definitions, utility functions (sanitize_for_json, safe_json_dumps), todo parsing logic (parse_todos_from_tool_output), and the tool list assembly — 596 lines in one file.

**Why it's wrong:** Adding or modifying a single tool requires reading the entire file. Todo parsing logic has nothing to do with tool definitions. The tool list at the bottom creates an ordering dependency on all functions above.

**Do this instead:** Separate by concern: `tools/` sub-files per tool category, `utils/json.py` for serialization utilities, `utils/todo_parser.py` for todo parsing. The registry replaces the explicit `tools = [...]` list.

### Anti-Pattern 3: Entry Point Owning Business Logic

**What people do:** Current `app.py` contains the `fig_inter` tool definition (lines 141-202), the `extract_data` tool (lines 94-140), the `Context` dataclass, the `identity_aware_prompt` middleware, and the `token_stream_events` generator — all alongside FastAPI routes.

**Why it's wrong:** The entry point becomes the hardest file to read and the most dangerous to change. Middleware is invisible unless you read the full `lifespan` function. Shared state (pandas DataFrames via `python_inter` `globals()`) lives in the wrong scope.

**Do this instead:** `main.py` does only: load config, import domain modules (triggering registration), call `create_app()`, call `uvicorn.run()`. Everything else moves to its natural home.

### Anti-Pattern 4: Hardcoded Internal IPs in Source Files

**What people do:** `knowledge_base.py` hardcodes `http://10.108.13.254:11434` and `qwen3-embedding:8b`. `subagent/call_api_tool.py` hardcodes `http://10.108.13.250:8001/predict_reason`. `tools.py` hardcodes `db_name = "dcma"`.

**Why it's wrong:** Different deployments (dev, staging, production) require source code changes. The values are not surfaced in any config, making them invisible to operators.

**Do this instead:** All service addresses, model names, database names, and similar deployment-specific values go into `config.yaml` (or `.env` for secrets). The config schema (`Pydantic`) validates on startup.

---

## Scaling Considerations

| Scale | Architecture Adjustments |
|-------|--------------------------|
| Current (single deployment) | Single process, `app.state.agent` singleton, `AsyncConnectionPool(min=2, max=10)`. No changes needed. |
| Multiple deployments (dev/staging/prod) | Config YAML per environment. Secrets via environment-specific `.env`. No code changes per deployment. |
| Multiple agent projects | Each project gets its own `projects/new_project/` directory and `main.py`. `agent_core/` shared without modification. |
| High concurrency (1k+ concurrent chats) | LangGraph async already handles concurrency via asyncio. PostgreSQL pool `max_size` tuning. SSE streaming design already correct. |
| Multiple agent instances per project | Stateless agent (checkpointer handles state). Multiple uvicorn workers already supported (`gunicorn -w 4`). |

---

## Sources

- LangChain 1.0 Agent Middleware Architecture: [deepwiki.com/langchain-ai/langchain/4.1-agent-system-with-middleware](https://deepwiki.com/langchain-ai/langchain/4.1-agent-system-with-middleware) — HIGH confidence
- FastAPI Application Factory Pattern: [sqr-072.lsst.io](https://sqr-072.lsst.io/) — HIGH confidence
- Python Protocol vs ABC for clean architecture: [medium.com/@pouyahallaj/introduction-1616b3a4a637](https://medium.com/@pouyahallaj/introduction-1616b3a4a637) — HIGH confidence
- FastAPI lifespan + app.state singleton pattern: [fastapi.tiangolo.com/advanced/events/](https://fastapi.tiangolo.com/advanced/events/) — HIGH confidence
- Python Registry Pattern: [dev.to/dentedlogic](https://dev.to/dentedlogic/stop-writing-giant-if-else-chains-master-the-python-registry-pattern-ldm) — MEDIUM confidence
- pydantic-settings YAML config: [docs.pydantic.dev/latest/concepts/pydantic_settings/](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) — HIGH confidence
- Codebase analysis (direct source read): `app.py`, `tools.py`, `knowledge_base.py` — HIGH confidence

---

*Architecture research for: Modular Python AI Agent Framework refactor*
*Researched: 2026-03-26*
