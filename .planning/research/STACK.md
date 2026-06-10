# Stack Research

**Domain:** Modular Python AI Agent Framework (LangChain 1.0 + LangGraph 1.0 refactoring)
**Researched:** 2026-03-26
**Confidence:** HIGH (core patterns), MEDIUM (LangChain 1.0 specific API details)

---

## Context

This is a **refactoring project**, not a greenfield build. The existing stack (LangChain 1.0.3,
LangGraph 1.0.5, FastAPI 0.121.0, Python 3.12) is fixed by constraint. No new framework-level
dependencies. This document covers **patterns and techniques** for achieving modularity within
those constraints, not new technology choices.

---

## Pattern 1: Python Project Structure for Plugin Architecture

### Recommendation: Flat Package Registry (No entry_points)

**Why not entry_points:** Entry points are for distributing plugins across separately-installed
PyPI packages. This project is a monorepo — all modules live in the same codebase and are not
published as pip packages. Using `importlib.metadata` entry_points would add complexity
(pyproject.toml groups, package installs) for zero benefit.

**Why not namespace packages / pkgutil discovery:** Namespace-based auto-discovery (scanning
`plugins/*` subdirectories) adds implicit magic. The project has a known, bounded set of modules
(fault-diagnosis domain + framework core). Explicit registration is clearer and easier to trace
for solo/small-team development.

**Use instead: Explicit dict-based registry with Python Protocol for interface enforcement.**

### Recommended Directory Layout

```
fault-diagnosis/          # repo root
├── core/                 # Shared framework — never project-specific
│   ├── __init__.py
│   ├── agent.py          # create_agent() wrapper, AgentFactory
│   ├── middleware/
│   │   ├── __init__.py   # Re-exports built-in middleware
│   │   └── base.py       # AgentMiddleware re-export + helpers
│   ├── rag/
│   │   ├── __init__.py
│   │   └── base.py       # KnowledgeBase Protocol/ABC
│   ├── tools/
│   │   ├── __init__.py
│   │   └── registry.py   # ToolRegistry class
│   └── config.py         # AgentConfig (pydantic-settings BaseSettings)
│
├── projects/             # Each subdirectory is a deployable project
│   └── fault_diagnosis/  # Current project, thin wrapper
│       ├── __init__.py
│       ├── main.py       # FastAPI app factory (calls core.agent.create_agent)
│       ├── config.py     # Project-specific config (extends core AgentConfig)
│       ├── tools.py      # @tool functions for this domain
│       ├── middleware.py  # @dynamic_prompt + custom middleware for this domain
│       ├── knowledge_base.py  # FAISSKnowledgeBase(KnowledgeBase) for this domain
│       └── prompts.py    # System prompt string
│
├── app.py                # Thin shim: from projects.fault_diagnosis.main import app
├── requirements.txt      # Unchanged
└── agent_fronted/        # Unchanged (frontend)
```

**Key principle:** `core/` has zero project-specific knowledge. `projects/fault_diagnosis/` has
zero framework code. `app.py` stays as a one-liner shim so the existing deployment command
(`gunicorn app:app`) never changes.

### ToolRegistry Implementation

```python
# core/tools/registry.py
from typing import Protocol, runtime_checkable
from langchain_core.tools import BaseTool

class ToolRegistry:
    """Explicit registry for agent tools. No magic discovery."""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> BaseTool:
        """Register a tool. Usable as a decorator or called directly."""
        self._tools[tool.name] = tool
        return tool

    def get_all(self) -> list[BaseTool]:
        return list(self._tools.values())

    def get(self, name: str) -> BaseTool:
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' not registered. Available: {list(self._tools)}")
        return self._tools[name]
```

Usage in a project's tools file:

```python
# projects/fault_diagnosis/tools.py
from langchain_core.tools import tool
from core.tools.registry import ToolRegistry

registry = ToolRegistry()

@registry.register
@tool
def sql_inter(query: str) -> str:
    """Query the MySQL sensor database."""
    ...
```

Usage when assembling the agent:

```python
# projects/fault_diagnosis/main.py
from core.agent import create_agent_app
from projects.fault_diagnosis.tools import registry
from projects.fault_diagnosis.middleware import identity_aware_prompt, TodoListMiddleware

app = create_agent_app(
    tools=registry.get_all(),
    middleware=[TodoListMiddleware(), identity_aware_prompt],
    ...
)
```

**Confidence: HIGH** — dict-based registry is a well-established Python pattern with no external
dependencies. Verified against Python stdlib and LangChain tool structure.

---

## Pattern 2: LangChain 1.0 Middleware — Custom Middleware & Composition

### AgentMiddleware Base Class (Verified: LangChain 1.0.x)

LangChain 1.0 provides `langchain.agents.middleware.AgentMiddleware` as the base class.
Subclass it and override any subset of the hook methods.

```python
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest

class CustomMiddleware(AgentMiddleware):
    # Optional: extend agent state with custom fields
    # WARNING: in LangChain 1.0.3, passing state_schema to create_agent()
    # AND using middleware is mutually exclusive (Issue #33217).
    # Workaround: define state_schema ON the middleware class, not in create_agent().
    state_schema = MyCustomState  # optional

    def before_model(self, state, runtime):
        """Runs before each LLM call. Return dict to update state, or None."""
        ...
        return None

    def after_model(self, state, runtime):
        """Runs after each LLM call (reverse order). Return dict or None."""
        ...
        return None

    async def awrap_model_call(self, request: ModelRequest, handler):
        """Wraps the actual LLM call. Use for retry, fallback, caching."""
        try:
            return await handler(request)
        except Exception:
            return await handler(request)  # simple retry example
```

### Hook Signatures (Verified from LangChain source and reference docs)

| Method | Signature | Order | Use For |
|--------|-----------|-------|---------|
| `before_agent` | `(state, runtime) -> dict \| None` | first → last | One-time setup per invocation |
| `before_model` | `(state, runtime) -> dict \| None` | first → last | Modify state before LLM call |
| `after_model` | `(state, runtime) -> dict \| None` | last → first (reversed) | Inspect/transform LLM output |
| `after_agent` | `(state, runtime) -> dict \| None` | last → first (reversed) | Cleanup, logging |
| `wrap_model_call` | `(request, handler) -> response` | first = outermost | Retry, fallback, caching |
| `awrap_model_call` | async version | same | Preferred for async agents |
| `wrap_tool_call` | `(request, handler) -> ToolMessage` | first = outermost | Tool error handling, audit |

**Execution order rule:** `before_*` hooks fire first-to-last (list order). `after_*` hooks fire
last-to-first (reverse list order). `wrap_*` hooks compose as nested layers — first middleware in
the list becomes outermost wrapper.

### `@dynamic_prompt` Decorator (Verified: LangChain reference docs)

The `@dynamic_prompt` decorator is a convenience that creates middleware whose sole purpose is
injecting a per-request system prompt. It wraps `wrap_model_call` internally.

```python
from langchain.agents.middleware import dynamic_prompt
from langchain.agents.middleware.types import ModelRequest

@dynamic_prompt
def identity_aware_prompt(request: ModelRequest) -> str:
    """Return system prompt string based on runtime context."""
    # Access context_schema values via request.context (or request.runtime.context)
    user_role = request.context.get("user_role", "guest")
    base_prompt = "You are an industrial equipment expert."
    if user_role == "admin":
        return f"{base_prompt} The user is an administrator with full access."
    return f"{base_prompt} The user is a guest; restrict sensitive operations."
```

The decorated function must accept `request: ModelRequest` and return `str | SystemMessage`.
The result becomes the system prompt for that specific LLM call.

### CRITICAL: `middleware` vs `state_schema` Constraint

**Issue:** In LangChain 1.0.3, `create_agent()` raises an assertion error if you pass both
`middleware` and `state_schema` simultaneously. This is a known limitation (GitHub Issue #33217,
closed "not planned" October 2025).

**Workaround (official):** Define custom state on the middleware class itself:

```python
from typing import TypedDict, NotRequired
from langchain.agents.middleware import AgentMiddleware

class TodoState(TypedDict):
    todos: NotRequired[list[str]]

class ProjectTodoMiddleware(AgentMiddleware):
    state_schema = TodoState  # State defined here, NOT in create_agent()
    ...
```

Then in `create_agent()`, omit `state_schema`:

```python
agent = create_agent(
    model=llm,
    tools=tools,
    middleware=[ProjectTodoMiddleware(), SummarizationMiddleware(...), identity_aware_prompt],
    context_schema=MyContextSchema,  # context_schema IS compatible with middleware
    checkpointer=checkpointer,
)
```

**Confidence: HIGH** — verified from LangChain GitHub issue, official docs, and reference API.
This constraint exists in 1.0.3. Do NOT attempt to pass both `middleware` and `state_schema`
to `create_agent()`.

### Middleware Composition for Multi-Project Framework

```python
# core/middleware/__init__.py
# Re-export built-in middleware so project code only imports from core
from langchain.agents.middleware import (
    TodoListMiddleware,
    SummarizationMiddleware,
    dynamic_prompt,
    AgentMiddleware,
)

__all__ = [
    "TodoListMiddleware",
    "SummarizationMiddleware",
    "dynamic_prompt",
    "AgentMiddleware",
]
```

Project-level assembly:

```python
# projects/fault_diagnosis/main.py
from core.middleware import TodoListMiddleware, SummarizationMiddleware, dynamic_prompt

MIDDLEWARE = [
    TodoListMiddleware(),
    SummarizationMiddleware(model=summary_llm, trigger=("tokens", 64_000), keep=("messages", 20)),
    identity_aware_prompt,        # @dynamic_prompt decorated function
]
```

Different projects assemble different middleware lists without touching framework code.

**Confidence: MEDIUM-HIGH** — composition pattern verified. Exact `SummarizationMiddleware`
parameter names should be re-checked if upgrading LangChain version.

---

## Pattern 3: Configuration-Driven Assembly

### Recommendation: pydantic-settings with TOML project config

**Why pydantic-settings:** Already in the existing stack (`pydantic-settings` 2.11.0). Provides
layered config: TOML file < environment variables < init kwargs. Type-safe with validation.
No new dependency needed.

**Why TOML, not YAML:** TOML is in the Python stdlib (`tomllib`, Python 3.11+). YAML requires
PyYAML. TOML is simpler for flat/nested config without the gotchas of YAML parsing edge cases.

**Why not code-only config:** A config file separates "which modules/middleware are active" from
framework code. A developer can onboard a new project by writing a TOML file + a `tools.py`,
without reading framework internals.

### Layered Config Design

```python
# core/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings import TomlConfigSettingsSource, PydanticBaseSettingsSource
from pydantic import BaseModel
from typing import Any

class MiddlewareConfig(BaseModel):
    name: str                    # e.g. "TodoListMiddleware"
    enabled: bool = True
    params: dict[str, Any] = {} # Passed to middleware __init__

class RAGConfig(BaseModel):
    enabled: bool = True
    embedding_model: str = "qwen3-embedding:8b"
    embedding_base_url: str = "http://localhost:11434"
    vector_store_path: str = "./faiss_db"
    top_k: int = 3

class AgentConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENT_",
        toml_file="agent.toml",
        extra="ignore",
    )

    project_name: str = "default"
    middleware: list[MiddlewareConfig] = []
    rag: RAGConfig = RAGConfig()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        return (init_settings, env_settings, dotenv_settings,
                file_secret_settings, TomlConfigSettingsSource(settings_cls))
```

```toml
# projects/fault_diagnosis/agent.toml
project_name = "fault_diagnosis"

[[middleware]]
name = "TodoListMiddleware"
enabled = true

[[middleware]]
name = "SummarizationMiddleware"
enabled = true
[middleware.params]
trigger = ["tokens", 64000]
keep = ["messages", 20]

[rag]
enabled = true
embedding_model = "qwen3-embedding:8b"
embedding_base_url = "http://10.108.13.254:11434"
vector_store_path = "./faiss_db"
top_k = 3
```

**Key constraint:** Middleware enabled/disabled via TOML, but the middleware class list must
still be code — Python cannot safely deserialize arbitrary class names from config without risk.
Use the config to control parameters and feature flags, not to load arbitrary classes.

**Safe assembly pattern:**

```python
# core/agent.py
MIDDLEWARE_REGISTRY: dict[str, type[AgentMiddleware]] = {
    "TodoListMiddleware": TodoListMiddleware,
    "SummarizationMiddleware": SummarizationMiddleware,
}

def build_middleware(config: list[MiddlewareConfig]) -> list[AgentMiddleware]:
    result = []
    for mw_conf in config:
        if not mw_conf.enabled:
            continue
        cls = MIDDLEWARE_REGISTRY.get(mw_conf.name)
        if cls is None:
            raise ValueError(f"Unknown middleware: {mw_conf.name}")
        result.append(cls(**mw_conf.params))
    return result
```

This is explicit (no `eval`, no `importlib` magic) and type-safe. New middleware is registered
in `MIDDLEWARE_REGISTRY`, not by adding TOML config alone — preventing accidental execution of
arbitrary code.

**Confidence: HIGH** — pydantic-settings TOML source is supported natively in pydantic-settings
2.x (no extra dependency; Python 3.11+ has `tomllib` in stdlib). Pattern verified against docs.

---

## Pattern 4: RAG / Knowledge Base Abstraction

### Recommendation: BaseRetriever subclass + @tool factory function

**Why BaseRetriever, not a custom ABC:** LangChain's `langchain_core.retrievers.BaseRetriever`
is already the standard interface. Subclassing it gives the standard `invoke`/`ainvoke`
Runnable interface for free, integrates with LangChain tracing, and is what all LangChain
retriever integrations use. Adding a custom ABC on top would be redundant.

**Why expose RAG as a @tool, not inject the retriever directly into the agent:** The existing
architecture already uses this pattern (`query_knowledge_base` tool in `tools.py`). Keeping
retrieval as a named tool means the agent can decide whether to query the knowledge base, with
full observability in LangSmith traces. Direct retriever injection into the agent graph would
bypass this decision.

### KnowledgeBase Abstraction Pattern

```python
# core/rag/base.py
from abc import ABC, abstractmethod
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

class KnowledgeBase(ABC):
    """Abstract interface for a project knowledge base.

    Implementations wrap a specific vector store + embedding model.
    Projects subclass this to configure their storage backend.
    """

    @abstractmethod
    def get_retriever(self) -> BaseRetriever:
        """Return a configured LangChain retriever."""
        ...

    @abstractmethod
    def build(self, source_dir: str) -> None:
        """(Re)build the index from source documents."""
        ...

    def as_tool(self, description: str = "Search the knowledge base"):
        """Return a @tool-decorated function wrapping this knowledge base."""
        from langchain_core.tools import tool
        retriever = self.get_retriever()

        @tool
        def query_knowledge_base(query: str) -> str:
            """Search project knowledge base for relevant information."""
            docs: list[Document] = retriever.invoke(query)
            return "\n\n".join(
                f"[Page {d.metadata.get('page', '?')}]: {d.page_content}"
                for d in docs
            )

        query_knowledge_base.__doc__ = description
        return query_knowledge_base
```

Project-specific implementation:

```python
# projects/fault_diagnosis/knowledge_base.py
import os
from langchain_ollama import OllamaEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_community.document_loaders import PyPDFLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_core.retrievers import BaseRetriever
from core.rag.base import KnowledgeBase

class FAISSKnowledgeBase(KnowledgeBase):
    def __init__(
        self,
        embedding_model: str,
        embedding_base_url: str,
        vector_store_path: str,
        top_k: int = 3,
    ):
        self._embedding_model = embedding_model
        self._base_url = embedding_base_url
        self._path = vector_store_path
        self._top_k = top_k
        self._db: FAISS | None = None

    def _get_embeddings(self) -> OllamaEmbeddings:
        return OllamaEmbeddings(
            model=self._embedding_model,
            base_url=self._base_url,
        )

    def _load_db(self) -> FAISS:
        if self._db is None:
            self._db = FAISS.load_local(
                self._path,
                self._get_embeddings(),
                allow_dangerous_deserialization=True,
            )
        return self._db

    def get_retriever(self) -> BaseRetriever:
        db = self._load_db()
        return db.as_retriever(search_kwargs={"k": self._top_k})

    def build(self, source_dir: str) -> None:
        loader = PyPDFLoader(source_dir)
        docs = loader.load()
        splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
        chunks = splitter.split_documents(docs)
        db = FAISS.from_documents(chunks, self._get_embeddings())
        db.save_local(self._path)
        self._db = db
```

Usage in project main:

```python
# projects/fault_diagnosis/main.py
from projects.fault_diagnosis.knowledge_base import FAISSKnowledgeBase
from projects.fault_diagnosis.tools import registry
from core.config import AgentConfig

cfg = AgentConfig()
kb = FAISSKnowledgeBase(
    embedding_model=cfg.rag.embedding_model,
    embedding_base_url=cfg.rag.embedding_base_url,
    vector_store_path=cfg.rag.vector_store_path,
    top_k=cfg.rag.top_k,
)
registry.register(kb.as_tool("Search the equipment fault knowledge base"))
```

**Timeout handling note:** The existing code wraps `db_retriever.invoke(query)` in a
`concurrent.futures` timeout (8 seconds). This logic should be preserved in `FAISSKnowledgeBase.get_retriever()`
or in the tool wrapper. It is a known operational necessity, not incidental code.

**Confidence: HIGH** — BaseRetriever interface verified from LangChain core reference docs.
The pattern of exposing retrieval via `@tool` is the approach shown in LangChain 1.0 RAG docs.

---

## Core Technologies (Fixed — Do Not Change)

| Technology | Version | Role | Notes |
|------------|---------|------|-------|
| Python | 3.12.x | Runtime | Conda env `faultagent` |
| LangChain | 1.0.3 | Agent creation, middleware, tools | Fixed |
| LangGraph | 1.0.5 | Agent execution graph, streaming, checkpointing | Fixed |
| FastAPI | 0.121.0 | HTTP/SSE server | Fixed |
| pydantic-settings | 2.11.0 | Config management | Already installed |
| pydantic | 2.12.3 | Data validation, schemas | Already installed |
| langchain-core | (current) | BaseRetriever, Document, BaseTool | Already installed |

## Supporting Patterns (No New Dependencies)

| Pattern | Mechanism | Replaces |
|---------|-----------|---------|
| Tool Registry | `dict[str, BaseTool]` class | Hardcoded list in `app.py` |
| Middleware Registry | `dict[str, type[AgentMiddleware]]` | Hardcoded middleware list in lifespan |
| RAG Abstraction | `KnowledgeBase` ABC + `BaseRetriever` subclass | Hardcoded `knowledge_base.py` globals |
| Project Config | `pydantic-settings` + TOML file | Hardcoded env vars scattered across files |
| Dynamic Prompt | `@dynamic_prompt` decorator | Inline lambda in `create_agent()` |

---

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| `importlib.metadata` entry_points | Designed for cross-package plugin discovery. Overkill for a monorepo. Requires pyproject.toml package declarations. | Plain dict-based `ToolRegistry` |
| `pkgutil.iter_modules` namespace scanning | Auto-discovery is implicit; breaks when plugin fails to import silently. Hard to debug. | Explicit registration in project `main.py` |
| `eval()` / `importlib.import_module` for class names | Security risk and debugging nightmare. "Load class by string name" patterns are fragile. | Explicit `MIDDLEWARE_REGISTRY` dict in framework core |
| Passing `state_schema` to `create_agent()` when using `middleware` | LangChain 1.0.3 assertion error (Issue #33217). Silently impossible. | Set `state_schema` on the `AgentMiddleware` subclass |
| Custom ABC on top of `BaseRetriever` | Adds indirection. LangChain's `BaseRetriever` already IS the abstract interface. | Subclass `BaseRetriever` directly |
| YAML for config files | Requires PyYAML (new dependency). TOML is Python stdlib (3.11+). | TOML with `pydantic-settings` built-in `TomlConfigSettingsSource` |
| Changing FastAPI app structure / SSE endpoints | Frontend API contract must not change. All SSE event types must remain identical. | Wrap `create_agent()` in core; keep routes in project `main.py` |

---

## Version Compatibility Notes

| Constraint | Detail |
|------------|--------|
| `middleware` + `state_schema` in `create_agent()` | MUTUALLY EXCLUSIVE in LangChain 1.0.3. Use `AgentMiddleware.state_schema` class attribute instead. |
| `context_schema` + `middleware` | COMPATIBLE. Safe to use both in `create_agent()`. |
| `pydantic-settings` TOML support | Built-in via `TomlConfigSettingsSource`. No extra package needed. Python 3.11+ stdlib `tomllib` is used automatically. |
| `langchain_core.retrievers.BaseRetriever` | Stable public API. `_get_relevant_documents(query: str) -> list[Document]` is the only required method. |
| `dynamic_prompt` decorator | From `langchain.agents.middleware` (not `langchain_core`). Import path confirmed in reference docs. |

---

## Sources

- [LangChain custom middleware docs](https://docs.langchain.com/oss/python/langchain/middleware/custom) — hook signatures, AgentMiddleware structure
- [LangChain agents reference (create_agent)](https://docs.langchain.com/oss/python/langchain/agents) — parameter list, context_schema, middleware list
- [LangChain middleware built-in](https://docs.langchain.com/oss/python/langchain/middleware/built-in) — available middleware, composition pattern
- [AgentMiddleware source (GitHub)](https://github.com/langchain-ai/langchain/blob/90d015c841e76396b077b04aaeaa57bc388b2118/libs/langchain_v1/langchain/agents/middleware/types.py) — complete class definition, hook signatures — HIGH confidence
- [dynamic_prompt reference](https://reference.langchain.com/python/langchain/agents/middleware/types/dynamic_prompt) — decorator signature and decorated function contract
- [LangChain Issue #33217](https://github.com/langchain-ai/langchain/issues/33217) — middleware + state_schema mutual exclusion, workaround — HIGH confidence (official issue)
- [LangChain Issue #34156](https://github.com/langchain-ai/langchain/issues/34156) — custom state_schema within middleware context
- [LangChain middleware blog post](https://blog.langchain.com/agent-middleware/) — composition order, before/after hooks
- [DeepWiki: Agent System with Middleware](https://deepwiki.com/langchain-ai/langchain/4.1-agent-system-with-middleware) — hook execution order, ControlFlow, wrap_model_call nesting — MEDIUM confidence (third-party doc)
- [BaseRetriever reference](https://reference.langchain.com/python/langchain-core/retrievers/BaseRetriever) — abstract method signature, Runnable interface
- [LangChain RAG docs](https://docs.langchain.com/oss/python/langchain/rag) — @tool-based retrieval pattern
- [pydantic-settings YAML/TOML](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) — TomlConfigSettingsSource, settings_customise_sources — HIGH confidence
- [Modular monolith in Python](https://breadcrumbscollector.tech/modular-monolith-in-python/) — component structure, facade pattern, registry without entry_points
- [Python plugin architecture (DEV.to)](https://dev.to/charlesw001/plugin-architecture-in-python-jla) — registry vs entry_points comparison

---

*Stack research for: Modular Python AI Agent Framework Refactoring*
*Researched: 2026-03-26*
