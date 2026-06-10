# Phase 3: Tools Modularization - Research

**Researched:** 2026-03-26
**Domain:** Python module refactoring (tool function extraction + lazy initialization + subagent migration)
**Confidence:** HIGH

## Summary

Phase 3 is the largest structural change in the refactoring roadmap. It transforms the monolithic `tools.py` (397 lines) plus tool definitions in `app.py` (lines 62-204) into a `tools/` package with 5 domain-specific modules and a `subagent/` sub-package. The critical complexity points are: (1) preserving `globals()` namespace sharing between `extract_data` and `fig_inter` (they must be in the same file), (2) converting module-level database connections that execute at import time into lazy-initialized singletons, and (3) correctly updating all `__file__`-based path resolutions when files move from root to `tools/` or `tools/subagent/`.

The codebase currently has 74 passing tests (22 from Phase 1, 52 from Phase 2). The test infrastructure uses `pytest_configure` hooks to patch external dependencies before any production code imports. After moving tools into a package, the mock patch target paths in `conftest.py` remain unchanged because they target the library-level objects (`langchain_community.utilities.SQLDatabase.from_uri`, `langchain_community.agent_toolkits.SQLDatabaseToolkit`), not the importing module. However, some test files may reference `tools` or `app` module paths that need updating.

The user decided to delete `python_inter` (currently disabled/unregistered) and the CLI test code in the subagent. The `tools/__init__.py` will assemble the tools list and export it, making `from tools import tools` continue to work after the migration. The `sqltools` (SQLDatabaseToolkit-generated tools) will be lazily initialized and added to the tools list during app lifespan via `get_sqltools()`.

**Primary recommendation:** Execute in 3 waves -- (1) create the `tools/` package with all modules and lazy init, verify `python -c "from tools import tools"` works without DB, (2) migrate subagent to `tools/subagent/`, (3) delete old files, update `app.py` and `conftest.py`, run full test suite. Each wave should be independently verifiable.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **tools/ file grouping**: 5 files + subagent subdirectory as specified:
  - `tools/__init__.py` -- explicit imports from each module, assembles tools list
  - `tools/data_tools.py` -- extract_data, fig_inter (globals() sharing)
  - `tools/sql_tools.py` -- sql_inter, sqltools (SQLDatabaseToolkit generated)
  - `tools/kb_tools.py` -- query_knowledge_base
  - `tools/report_tools.py` -- save_report, save_html_report
  - `tools/utility_tools.py` -- get_time, search_tool (TavilySearch)
  - `tools/subagent/__init__.py` -- fault_explanation_tool definition + export
  - `tools/subagent/agent.py` -- create_fault_explanation_agent()
  - `tools/subagent/system_prompt.py` -- FAULT_EXPLANATION_SYSTEM_PROMPT
  - `tools/subagent/api_tool.py` -- query_fault_data_and_call_api, fig_inter(subagent version), tools list
- **python_inter deleted**: Dead code, not registered to tools list
- **Lazy initialization strategy**: Module-level `_db = None` + `_get_db()` / `get_sqltools()` singleton pattern
  - `tools/sql_tools.py`: SQLDatabase.from_uri() + ChatOpenAI() + SQLDatabaseToolkit() all lazy
  - `tools/subagent/api_tool.py`: Independent lazy loading, does NOT reuse sql_tools connection
  - `sql_inter` internal pymysql: Keeps current per-call pattern (connect/close in finally)
- **Subagent reorganization**:
  - Two `fig_inter` functions remain independent (main uses sns/pd, subagent uses np)
  - File renames: fault_explanation_agent.py -> agent.py, fault_explanation_system_prompt.py -> system_prompt.py, call_api_tool.py -> api_tool.py
  - CLI test code (`invoke_fault_explanation_agent` + `__main__`) deleted from agent.py
  - `api_style.md` moves to `tools/subagent/`
  - `__file__` path adjustment: tools/ files use `dirname(dirname(__file__))`, tools/subagent/ files use `dirname(dirname(dirname(__file__)))`
- **tools list assembly**:
  - `__init__.py` explicit imports, assembles tools list WITHOUT sqltools
  - Exports `get_sqltools()` function separately
  - `app.py` calls `tools.extend(get_sqltools())` in lifespan after DB ready
  - `app.py` no longer defines any tools (extract_data, fig_inter moved away)
  - `app.py` still uses `from tools import tools` + `from tools.sql_tools import get_sqltools`

### Claude's Discretion
- html_template.html reference path adjustment in report_tools.py (`__file__` level change)
- conftest.py mock patch path adaptation for new tools/ structure
- Individual tool function import statement specifics

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| TOOL-01 | Create tools/ directory with data_tools, sql_tools, kb_tools, report_tools, utility_tools | Full file-by-file mapping below with exact functions per module, import dependencies, and __file__ path adjustments |
| TOOL-02 | Module-level DB connections changed to lazy initialization | Lazy singleton pattern documented with code examples; both sql_tools.py and subagent/api_tool.py independently lazy |
| TOOL-03 | extract_data + fig_inter + python_inter in same tools/data_tools.py, globals() sharing works | python_inter deleted (user decision); extract_data + fig_inter globals() sharing analysis shows they MUST remain in same file; data_tools.py design documented |
| TOOL-04 | subagent/ migrated to tools/subagent/, fault_explanation_tool imports from new location | Complete file mapping from old subagent/ to tools/subagent/ with rename table, __file__ path changes, and import path updates |
| TOOL-05 | tools/__init__.py exports complete tools list, old tools.py deleted | __init__.py assembly pattern documented; sqltools lazy via get_sqltools(); app.py integration changes specified |
</phase_requirements>

## Standard Stack

No new libraries needed. Phase 3 is purely a structural refactoring within the existing codebase.

### Core (Existing, Pinned)
| Library | Version | Purpose | Why Pinned |
|---------|---------|---------|------------|
| langchain | 1.0.3 | Agent framework | CLAUDE.md constraint: do not upgrade |
| langgraph | 1.0.5 | Graph-based agent execution | CLAUDE.md constraint: do not upgrade |
| fastapi | 0.121.0 | HTTP API framework | CLAUDE.md constraint: do not upgrade |
| langchain-community | (current) | SQLDatabase, SQLDatabaseToolkit | Used for SQL tool generation |
| langchain-tavily | (current) | TavilySearch tool | Built-in search tool |

### Supporting (Used by Tools)
| Library | Version | Purpose | Which Module |
|---------|---------|---------|-------------|
| pymysql | (current) | Direct MySQL connections | sql_tools.py, subagent/api_tool.py |
| pandas | (current) | DataFrame handling | data_tools.py |
| matplotlib | (current) | Chart generation | data_tools.py, subagent/api_tool.py |
| seaborn | (current) | Statistical visualization | data_tools.py |
| numpy | (current) | Numerical arrays | subagent/api_tool.py |
| sqlalchemy | (current) | SQLAlchemy engine for pandas | data_tools.py |
| requests | (current) | HTTP API calls | subagent/api_tool.py |

## Architecture Patterns

### Target Project Structure
```
tools/
├── __init__.py          # Assembles and exports tools list + get_sqltools()
├── data_tools.py        # extract_data, fig_inter (globals() shared namespace)
├── sql_tools.py         # sql_inter, _get_db(), get_sqltools() (lazy init)
├── kb_tools.py          # query_knowledge_base
├── report_tools.py      # save_report, save_html_report
├── utility_tools.py     # get_time, search_tool (TavilySearch)
└── subagent/
    ├── __init__.py      # fault_explanation_tool definition + export
    ├── agent.py         # create_fault_explanation_agent()
    ├── system_prompt.py # FAULT_EXPLANATION_SYSTEM_PROMPT
    ├── api_tool.py      # query_fault_data_and_call_api, fig_inter(subagent), tools list
    └── api_style.md     # API documentation (moved from subagent/)
```

### Pattern 1: Lazy Singleton for Database Connections

**What:** Replace module-level `db = SQLDatabase.from_uri(...)` (which executes at import time and requires live MySQL) with a function that creates the connection on first call.

**When to use:** Any module-level initialization that contacts external services.

**Current code (tools.py:29-44, BREAKS on import without MySQL):**
```python
host = os.getenv('HOST')
user = os.getenv('USER')
mysql_pw = os.getenv('MYSQL_PW')
port = os.getenv('PORT')
db = SQLDatabase.from_uri(f"mysql+pymysql://{user}:{mysql_pw}@{host}:{port}/{DCMA_DB_NAME}")
model = ChatOpenAI(...)
toolkit = SQLDatabaseToolkit(db=db, llm=model)
sqltools = toolkit.get_tools()
```

**Target code (tools/sql_tools.py):**
```python
_db = None
_model = None
_toolkit = None
_sqltools = None

def _get_db():
    global _db
    if _db is None:
        load_dotenv(override=True)
        host = os.getenv('HOST')
        user = os.getenv('USER')
        mysql_pw = os.getenv('MYSQL_PW')
        port = os.getenv('PORT')
        _db = SQLDatabase.from_uri(
            f"mysql+pymysql://{user}:{mysql_pw}@{host}:{port}/{DCMA_DB_NAME}"
        )
    return _db

def get_sqltools():
    """Return SQLDatabaseToolkit tools. Called during app lifespan when DB is ready."""
    global _sqltools
    if _sqltools is None:
        db = _get_db()
        model = ChatOpenAI(
            model=os.getenv("MODEL_NAME"),
            base_url=os.getenv("OPENAI_BASE_URL"),
            api_key=os.getenv("OPENAI_API_KEY"),
            temperature=0.7,
        )
        toolkit = SQLDatabaseToolkit(db=db, llm=model)
        _sqltools = toolkit.get_tools()
    return _sqltools
```

**Confidence:** HIGH -- This is a standard Python lazy initialization pattern. The existing `knowledge_base.py` already uses a similar `db_retriever = None` + `init_knowledge_base()` pattern.

### Pattern 2: globals() Namespace Sharing

**What:** `extract_data` stores DataFrames in `globals()`, and `fig_inter` reads them from the same namespace via `globals()`. Both functions MUST be in the same `.py` file for this to work.

**Why it matters:** `globals()` returns the module's own namespace dictionary. If `extract_data` is in `data_tools.py` and `fig_inter` is in a different file, `fig_inter`'s `globals()` call would return a different namespace that doesn't contain the DataFrames.

**Implementation in data_tools.py:**
```python
# extract_data writes:
globals()[df_name] = df

# fig_inter reads:
g = globals()
exec(py_code, g, local_vars)
```

Both functions are defined in the same file, so `globals()` refers to `tools.data_tools` module globals. This is the same mechanism as the current `app.py` where both are defined.

**Confidence:** HIGH -- This is how Python module namespaces work; verified by reading the actual code.

### Pattern 3: __file__ Path Resolution After Move

**What:** Several tools use `os.path.dirname(__file__)` to locate `agent_fronted/public/` or `html_template.html`. When files move from project root to `tools/`, the dirname calculation changes.

**Current paths (from project root):**
```python
# tools.py (at project root):
os.path.dirname(__file__)                          # -> /project_root/
os.path.join(os.path.dirname(__file__), "agent_fronted")  # -> /project_root/agent_fronted

# subagent/call_api_tool.py (one level deep):
os.path.dirname(os.path.dirname(__file__))         # -> /project_root/
```

**Target paths:**
```python
# tools/data_tools.py (one level deep):
os.path.dirname(os.path.dirname(__file__))         # -> /project_root/
# So: os.path.join(os.path.dirname(os.path.dirname(__file__)), "agent_fronted")

# tools/report_tools.py (one level deep):
os.path.dirname(os.path.dirname(__file__))         # -> /project_root/
# For html_template.html: os.path.join(os.path.dirname(os.path.dirname(__file__)), "html_template.html")

# tools/subagent/api_tool.py (two levels deep):
os.path.dirname(os.path.dirname(os.path.dirname(__file__)))  # -> /project_root/
```

**Specific files affected:**

| File | Current `__file__` usage | New path expression |
|------|--------------------------|---------------------|
| `tools/data_tools.py` (fig_inter) | `os.path.dirname(__file__)` for `agent_fronted` | `os.path.dirname(os.path.dirname(__file__))` |
| `tools/data_tools.py` (extract_data) | N/A (no __file__ usage, uses SQLAlchemy engine) | No change |
| `tools/report_tools.py` (save_report) | `os.path.dirname(__file__)` for `agent_fronted` | `os.path.dirname(os.path.dirname(__file__))` |
| `tools/report_tools.py` (save_html_report) | `os.path.dirname(__file__)` for `html_template.html` AND `agent_fronted` | `os.path.dirname(os.path.dirname(__file__))` for both |
| `tools/subagent/api_tool.py` (fig_inter) | `os.path.dirname(os.path.dirname(__file__))` for `agent_fronted` | `os.path.dirname(os.path.dirname(os.path.dirname(__file__)))` |

**Confidence:** HIGH -- Verified by reading actual code and counting directory levels.

### Pattern 4: tools/__init__.py Assembly

**What:** The `__init__.py` explicitly imports tool functions from each submodule and assembles the `tools` list. `sqltools` is NOT included because it requires live DB.

```python
"""tools/ package -- exports tools list and get_sqltools()."""

from tools.data_tools import extract_data, fig_inter
from tools.sql_tools import sql_inter, get_sqltools
from tools.kb_tools import query_knowledge_base
from tools.report_tools import save_report, save_html_report
from tools.utility_tools import get_time, search_tool
from tools.subagent import fault_explanation_tool

tools = [
    search_tool,
    sql_inter,
    query_knowledge_base,
    save_report,
    save_html_report,
    get_time,
    fault_explanation_tool,
    extract_data,
    fig_inter,
]
```

Note: The current `tools.py` has `tools.extend(sqltools)` at module level (line 397), and `app.py` has `tools.extend([extract_data, fig_inter])` at line 204. After migration:
- `extract_data` and `fig_inter` are included directly in `__init__.py`'s tools list
- `sqltools` is added in `app.py`'s lifespan via `tools.extend(get_sqltools())`

**Confidence:** HIGH -- Matches user's locked decision.

### Anti-Patterns to Avoid

- **Circular imports**: `tools/__init__.py` imports from submodules. Submodules must NOT import from `tools` package or each other (except subagent internal imports). If `kb_tools.py` needs knowledge_base, it imports directly: `from knowledge_base import db_retriever`.
- **Eager initialization in __init__.py**: Do NOT call `get_sqltools()` at module level in `__init__.py`. It must remain a function called during lifespan.
- **Breaking the tools list identity**: `app.py` does `from tools import tools` and later `tools.extend(get_sqltools())`. This mutates the same list object. After the extend, the agent creation sees the full list. This works because Python lists are mutable references. Do NOT reassign `tools = [...]` after the initial assignment in `__init__.py`.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Connection pooling | Custom connection pool | Existing per-call pymysql pattern | sql_inter already creates/closes connections per call; this is intentional |
| Tool registry | Auto-discovery/plugin system | Explicit imports in __init__.py | User deferred ADVF-01 (tool auto-discovery) to v2 |
| Config validation | Runtime config checks | Simple os.getenv with defaults in config.py | User deferred ADVF-02 (pydantic-settings) to v2 |

## Common Pitfalls

### Pitfall 1: Import-Time Side Effects Blocking Tests
**What goes wrong:** Moving code into `tools/` modules but keeping module-level `SQLDatabase.from_uri()` or `TavilySearch()` calls causes import failures when MySQL/Tavily API is unavailable.
**Why it happens:** Python executes all top-level statements when a module is imported. The current `tools.py` lines 29-44 create a live MySQL connection at import time.
**How to avoid:** Wrap ALL external service connections in lazy functions. The `search_tool = TavilySearch(...)` in `utility_tools.py` is safe because `conftest.py` already patches `langchain_tavily.TavilySearch` before import. But the SQLDatabase connection must be lazy.
**Warning signs:** `from tools import tools` throws `pymysql.err.OperationalError` or `sqlalchemy.exc.OperationalError` in test environment.

### Pitfall 2: __file__ Path Off-By-One
**What goes wrong:** Reports/images are saved to wrong directory or html_template.html is not found.
**Why it happens:** When a file moves from root to `tools/`, `os.path.dirname(__file__)` returns `tools/` instead of project root. Need one extra `os.path.dirname()` call.
**How to avoid:** For each file using `__file__`, count the new directory depth and add corresponding `os.path.dirname()` wrappers. Test by printing the resolved path.
**Warning signs:** `FileNotFoundError` for `html_template.html`, images appearing in `tools/agent_fronted/` instead of `agent_fronted/`.

### Pitfall 3: conftest.py Patch Targets After Module Move
**What goes wrong:** Tests fail because mocks don't take effect -- the real MySQL connection is attempted.
**Why it happens:** `conftest.py` patches `langchain_community.utilities.SQLDatabase.from_uri` at the library level. This works regardless of which module imports it. BUT if any new module also does a module-level import of the return value and caches it, the patch must happen before that module is loaded.
**How to avoid:** The existing `pytest_configure` hook runs before test collection, which means before any `from tools import ...` in test files. This should continue to work. Verify by running `pytest --co` after migration.
**Warning signs:** First test collection/import fails with connection errors.

### Pitfall 4: globals() Sharing Broken by Accidental Separation
**What goes wrong:** `fig_inter` cannot access DataFrames created by `extract_data`.
**Why it happens:** If someone puts extract_data in one module and fig_inter in another, `globals()` returns different namespaces.
**How to avoid:** Both MUST be in `tools/data_tools.py`. Add a comment explaining why.
**Warning signs:** `fig_inter` returns "未找到图对象" even after `extract_data` successfully created a DataFrame.

### Pitfall 5: Mutating tools List After Agent Creation
**What goes wrong:** `get_sqltools()` is called after `create_agent()`, so the agent doesn't see the SQL tools.
**Why it happens:** Python lists are mutable, but `create_agent()` may copy the list internally.
**How to avoid:** Call `tools.extend(get_sqltools())` BEFORE `create_agent()` in the lifespan. The current code already adds tools before agent creation (line 204 in app.py runs at module level, before lifespan). The new pattern must maintain this order in lifespan.
**Warning signs:** Agent cannot find or invoke SQL toolkit tools.

### Pitfall 6: Subagent Import Paths After Move
**What goes wrong:** `from subagent.call_api_tool import tools` fails because subagent is now at `tools/subagent/`.
**Why it happens:** The old `subagent/` is a top-level package. After moving to `tools/subagent/`, internal imports must use `from tools.subagent.api_tool import tools` or relative imports.
**How to avoid:** Use relative imports within `tools/subagent/`: `from .api_tool import tools` in `agent.py`, `from .system_prompt import FAULT_EXPLANATION_SYSTEM_PROMPT`.
**Warning signs:** `ModuleNotFoundError: No module named 'subagent'`.

## Code Examples

### Example 1: tools/sql_tools.py with Lazy Init

```python
"""SQL query tools with lazy database initialization."""
import os
import json
import pymysql
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from langchain_core.tools import tool
from langchain_community.utilities import SQLDatabase
from langchain_community.agent_toolkits import SQLDatabaseToolkit
from langchain_openai import ChatOpenAI
from config import DCMA_DB_NAME

# === Lazy-initialized singletons ===
_db = None
_sqltools = None


def _get_db():
    """懒加载 SQLDatabase 单例。"""
    global _db
    if _db is None:
        load_dotenv(override=True)
        host = os.getenv('HOST')
        user = os.getenv('USER')
        mysql_pw = os.getenv('MYSQL_PW')
        port = os.getenv('PORT')
        _db = SQLDatabase.from_uri(
            f"mysql+pymysql://{user}:{mysql_pw}@{host}:{port}/{DCMA_DB_NAME}"
        )
    return _db


def get_sqltools():
    """返回 SQLDatabaseToolkit 生成的工具列表。在 app lifespan 中 DB 就绪后调用。"""
    global _sqltools
    if _sqltools is None:
        db = _get_db()
        model = ChatOpenAI(
            model=os.getenv("MODEL_NAME"),
            base_url=os.getenv("OPENAI_BASE_URL"),
            api_key=os.getenv("OPENAI_API_KEY"),
            temperature=0.7,
        )
        toolkit = SQLDatabaseToolkit(db=db, llm=model)
        _sqltools = toolkit.get_tools()
    return _sqltools


# === sql_inter tool (per-call connection, unchanged) ===
class SQLQuerySchema(BaseModel):
    sql_query: str = Field(description="SQL查询语句，建议使用LIMIT限制返回行数")


@tool(args_schema=SQLQuerySchema)
def sql_inter(sql_query: str) -> str:
    """在机械臂数据库上执行SQL查询。..."""
    # ... (unchanged body, creates per-call pymysql connection)
```

### Example 2: tools/subagent/__init__.py

```python
"""Fault explanation sub-agent tool -- entry point for the sub-agent."""
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field
from langchain_core.tools import tool
from .agent import create_fault_explanation_agent


class FaultExplanationSchema(BaseModel):
    task_description: str = Field(description="故障分析任务描述，如：'分析J3轴故障的原因'")


@tool(args_schema=FaultExplanationSchema)
def fault_explanation_tool(task_description: str) -> str:
    """调用Fault_explanation模型进行设备故障诊断分析..."""
    try:
        sub_agent = create_fault_explanation_agent()
        result = sub_agent.invoke(
            {"messages": [HumanMessage(content=task_description)]},
        )
        if result and "messages" in result and len(result["messages"]) > 0:
            return result["messages"][-1].content
        else:
            return "Fault_explanation模型未返回有效结果"
    except Exception as e:
        return f"调用Fault_explanation模型失败：{str(e)}"
```

### Example 3: tools/subagent/agent.py (with relative imports)

```python
"""Create the fault explanation sub-agent."""
import os
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from dotenv import load_dotenv

from .api_tool import tools
from .system_prompt import FAULT_EXPLANATION_SYSTEM_PROMPT

load_dotenv(override=True)


def create_fault_explanation_agent():
    """创建故障解释子Agent"""
    model = ChatOpenAI(
        model=os.getenv("MODEL_NAME"),
        base_url=os.getenv("OPENAI_BASE_URL"),
        api_key=os.getenv("OPENAI_API_KEY"),
        temperature=0.7,
    )
    agent = create_agent(
        model=model,
        tools=tools,
        system_prompt=FAULT_EXPLANATION_SYSTEM_PROMPT,
    )
    return agent
```

### Example 4: app.py Changes (lifespan)

```python
# At top of app.py:
from tools import tools
from tools.sql_tools import get_sqltools

# In lifespan, BEFORE create_agent:
tools.extend(get_sqltools())

agent = create_agent(
    model=model,
    tools=tools,
    # ...
)
```

## File-by-File Migration Map

### Source -> Destination

| Source File | Source Lines | Destination | What Moves |
|-------------|-------------|-------------|------------|
| tools.py:25 | `search_tool = TavilySearch(...)` | tools/utility_tools.py | TavilySearch instantiation |
| tools.py:28-44 | SQLDatabase + ChatOpenAI + Toolkit | tools/sql_tools.py | Becomes lazy `_get_db()` + `get_sqltools()` |
| tools.py:47-78 | `query_knowledge_base` | tools/kb_tools.py | Unchanged logic |
| tools.py:80-138 | `sql_inter` | tools/sql_tools.py | Unchanged logic |
| tools.py:140-178 | `fault_explanation_tool` | tools/subagent/__init__.py | Moves with subagent |
| tools.py:180-185 | `get_time` | tools/utility_tools.py | Unchanged logic |
| tools.py:187-298 | `save_report` | tools/report_tools.py | __file__ path updated |
| tools.py:300-383 | `save_html_report` | tools/report_tools.py | __file__ path updated |
| tools.py:385-397 | `tools = [...]` + `tools.extend(sqltools)` | tools/__init__.py | Reassembled; sqltools removed |
| app.py:63-94 | `python_inter` | DELETED | Dead code (user decision) |
| app.py:96-201 | `extract_data`, `fig_inter` | tools/data_tools.py | __file__ path updated |
| app.py:204 | `tools.extend([extract_data, fig_inter])` | DELETED | Now in __init__.py |
| subagent/fault_explanation_agent.py | Entire file | tools/subagent/agent.py | CLI code deleted, imports updated |
| subagent/fault_explanation_system_prompt.py | Entire file | tools/subagent/system_prompt.py | Unchanged |
| subagent/call_api_tool.py | Entire file | tools/subagent/api_tool.py | Module-level DB becomes lazy, __file__ updated |
| subagent/api_style.md | Entire file | tools/subagent/api_style.md | Moved as-is |

### Files to Delete After Migration
- `tools.py` (root level)
- `subagent/` (entire directory)

### Imports to Update

| File | Old Import | New Import |
|------|-----------|------------|
| app.py:33 | `from tools import tools` | `from tools import tools` (UNCHANGED -- tools becomes package) |
| app.py:34 | `from utils import ...` | Unchanged |
| app.py (new) | N/A | `from tools.sql_tools import get_sqltools` (new line) |
| tools/subagent/agent.py | `from subagent.call_api_tool import tools` | `from .api_tool import tools` |
| tools/subagent/agent.py | `from subagent.fault_explanation_system_prompt import ...` | `from .system_prompt import FAULT_EXPLANATION_SYSTEM_PROMPT` |

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (via pytest.ini) |
| Config file | pytest.ini |
| Quick run command | `python3 -m pytest tests/ -x -q` |
| Full suite command | `python3 -m pytest tests/ -v` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| TOOL-01 | tools/ directory with 5 modules exists, each importable | smoke | `python3 -c "from tools.data_tools import extract_data, fig_inter; from tools.sql_tools import sql_inter; from tools.kb_tools import query_knowledge_base; from tools.report_tools import save_report, save_html_report; from tools.utility_tools import get_time, search_tool"` | N/A (inline) |
| TOOL-02 | `from tools import tools` works without MySQL/Ollama | smoke | `python3 -c "from tools import tools; print(f'tools count: {len(tools)}')"` | N/A (inline) |
| TOOL-03 | extract_data + fig_inter globals() sharing | unit | `python3 -m pytest tests/test_tools_structure.py -x -q` | Wave 0 |
| TOOL-04 | subagent at tools/subagent/, fault_explanation_tool importable | smoke | `python3 -c "from tools.subagent import fault_explanation_tool"` | N/A (inline) |
| TOOL-05 | tools/__init__.py exports full list, old files deleted | unit | `python3 -m pytest tests/test_tools_structure.py -x -q` | Wave 0 |

### Sampling Rate
- **Per task commit:** `python3 -m pytest tests/ -x -q`
- **Per wave merge:** `python3 -m pytest tests/ -v`
- **Phase gate:** Full suite green + `python3 -c "from tools import tools"` succeeds in clean env

### Wave 0 Gaps
- [ ] `tests/test_tools_structure.py` -- covers TOOL-01 through TOOL-05: module structure validation, lazy init verification, tools list composition, old files deleted, __file__ paths correct
- [ ] conftest.py patch path updates -- existing patches target library-level (`langchain_community.utilities.SQLDatabase.from_uri`), which should still work. But subagent patches may need additions for the lazy init pattern.

## Project Constraints (from CLAUDE.md)

- **Tech Stack**: LangChain 1.0.3 + LangGraph 1.0.5 + FastAPI 0.121.0 -- do not upgrade
- **API Contract**: Do not change existing HTTP endpoints
- **No new heavy dependencies**: This phase adds zero dependencies
- **Language**: All comments, docstrings, and user-facing strings in Chinese (Simplified)
- **Python style**: snake_case, 4-space indent, double quotes preferred
- **Environment**: python-dotenv from `.env` at project root; never read `.env` contents (secrets)

## Open Questions

1. **TavilySearch instantiation in utility_tools.py**
   - What we know: Currently `search_tool = TavilySearch(max_results=5, topic="general")` is at module level in tools.py:25. The conftest.py patches `langchain_tavily.TavilySearch` before import, so this works in tests.
   - What's unclear: Whether moving this to `tools/utility_tools.py` changes the patch timing. The `pytest_configure` hook should still run before any test collection imports.
   - Recommendation: Keep it as module-level in utility_tools.py. The conftest patch targets the class itself (`langchain_tavily.TavilySearch`), so it works regardless of which module instantiates it. LOW risk.

2. **conftest.py patch for subagent's lazy SQLDatabase**
   - What we know: The subagent/call_api_tool.py currently does module-level `SQLDatabase.from_uri()`. After migration to lazy init, this no longer runs at import time.
   - What's unclear: Whether existing tests directly or indirectly trigger `fault_explanation_tool` which would call the lazy init.
   - Recommendation: The existing `langchain_community.utilities.SQLDatabase.from_uri` patch should cover it since it patches the class method. If any test triggers the lazy init, the mock will be returned. No additional patches needed.

## Sources

### Primary (HIGH confidence)
- Direct code reading: `tools.py` (397 lines), `app.py` (580 lines), `subagent/` (3 files), `tests/conftest.py`, `config.py`, `knowledge_base.py`
- Phase 2 verification report confirming 74 tests pass and current state of code
- CONTEXT.md user decisions (locked choices for file structure, lazy init pattern, subagent organization)

### Secondary (MEDIUM confidence)
- Python `globals()` behavior: standard CPython semantics for module-level namespace
- `os.path.dirname(__file__)` resolution: standard Python path handling

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new libraries, existing pinned versions
- Architecture: HIGH -- all decisions locked by user in CONTEXT.md, verified against actual code
- Pitfalls: HIGH -- identified from direct code analysis of import chains, __file__ paths, and globals() usage
- Migration map: HIGH -- every function traced from source to destination with line numbers

**Research date:** 2026-03-26
**Valid until:** 2026-04-26 (stable -- purely internal refactoring, no external API changes)
