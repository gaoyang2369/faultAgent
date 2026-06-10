# Phase 2: Config & Utils - Research

**Researched:** 2026-03-26
**Domain:** Python module extraction (config centralization + utility function extraction)
**Confidence:** HIGH

## Summary

Phase 2 is a straightforward extraction phase: pull hardcoded configuration values into `config.py` and move generic utility functions from `tools.py` into `utils.py`. No new libraries are needed. The primary risk is breaking the 22 existing Phase 1 tests by changing import paths or accidentally triggering side effects during module initialization.

The codebase has 8 hardcoded values scattered across 4 files (`app.py`, `tools.py`, `knowledge_base.py`, `subagent/call_api_tool.py`). The utility functions (`sanitize_for_json`, `safe_json_dumps`, and the `parse_todos` family totaling ~195 lines) are cleanly separable from `tools.py` since they have no tool-specific dependencies. The `app.py` imports these 3 functions directly from `tools.py` (line 33), so updating that single import line is the critical integration point.

**Primary recommendation:** Create `config.py` as a simple module with top-level constants loaded from `os.getenv()` with sensible defaults, and `utils.py` as a pure-function module with zero external dependencies beyond stdlib + `langchain_core.messages`. Update imports in `app.py`, `tools.py`, and `knowledge_base.py`. Run full test suite after each file change.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **Not using** agent_core/ + projects/ dual-layer architecture
- **Not defining** Protocol interfaces (KnowledgeBaseProtocol, PromptProvider, etc.)
- **Not using** pydantic-settings for config inheritance
- **Using** modular restructuring within existing project: extract replaceable parts into independent modules
- **Target directory structure** defined in CONTEXT.md (config.py, utils.py at project root)
- **globals() sharing**: extract_data, fig_inter, python_inter must stay in same file (tools/data_tools.py) -- but this is Phase 3, not Phase 2
- **Module-level DB connection**: refactor to lazy initialization -- but this is Phase 3, not Phase 2
- **Subagent**: move to tools/subagent/ -- but this is Phase 3, not Phase 2

### Claude's Discretion
- config.py field organization and structure
- utils.py function selection
- How knowledge_base.py reads from config.py
- middleware.py implementation (Phase 4, not Phase 2)
- app.py internal organization after slimming (Phase 5, not Phase 2)

### Deferred Ideas (OUT OF SCOPE)
- pydantic-settings configuration validation
- Tool auto-discovery mechanism
- Project scaffold template (cookiecutter)
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| CONF-01 | Create config.py with all hardcoded config values (Ollama URL, embedding model, FAISS path, max_tokens, messages_to_keep, recursion_limit, DCMA db name, ML API URL) | Inventory of all 8 hardcoded values with exact locations documented below |
| CONF-02 | Create utils.py with generic utility functions (sanitize_for_json, safe_json_dumps, parse_todos series) | Utility function inventory with dependency analysis showing clean extractability |
| CONF-03 | Update app.py, tools.py, knowledge_base.py to import from config.py / utils.py | Import dependency map showing exactly which lines change in each file |
</phase_requirements>

## Standard Stack

No new libraries needed. This phase uses only existing project dependencies.

### Core (already installed)
| Library | Version | Purpose | Why Used |
|---------|---------|---------|----------|
| python-dotenv | 1.2.1 | Load .env variables | Already used throughout; config.py will centralize dotenv loading |
| os (stdlib) | -- | Environment variable access | `os.getenv()` for all config values |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Simple module constants | pydantic-settings | User explicitly deferred pydantic-settings (OUT OF SCOPE) |
| Module-level constants | dataclass config | Unnecessary complexity for 8 values; simple constants suffice |
| Separate config file formats (YAML/TOML) | Module constants | Config values are all env-var backed; no need for file-based config |

## Architecture Patterns

### config.py Structure

Use a flat module with constants loaded from environment variables at import time, with hardcoded defaults for non-secret values. This matches the existing pattern (all files already call `load_dotenv()` + `os.getenv()`).

```python
# config.py
import os
from dotenv import load_dotenv

load_dotenv(override=True)

# === Knowledge Base ===
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://10.108.13.254:11434")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "qwen3-embedding:8b")
FAISS_PATH = os.getenv("FAISS_PATH", "faiss_db")

# === Agent ===
MAX_TOKENS_BEFORE_SUMMARY = int(os.getenv("MAX_TOKENS_BEFORE_SUMMARY", "64000"))
MESSAGES_TO_KEEP = int(os.getenv("MESSAGES_TO_KEEP", "20"))
RECURSION_LIMIT = int(os.getenv("RECURSION_LIMIT", "50"))

# === Database ===
DCMA_DB_NAME = os.getenv("DCMA_DB_NAME", "dcma")

# === External APIs ===
FAULT_API_URL = os.getenv("FAULT_API_URL", "http://10.108.13.250:8001/predict_reason")
```

**Key design decisions:**
1. **Module-level constants, not a class** -- simple, no instantiation needed, matches Python convention for settings modules
2. **`load_dotenv()` at module top** -- called once when config is first imported; other modules stop calling `load_dotenv()` themselves
3. **Defaults for non-secret values** -- hardcoded defaults preserve current behavior; secret values (DB passwords, API keys) remain in `.env` only
4. **`int()` conversion** for numeric values -- catches type errors early at import time

### utils.py Structure

Pure utility module with no side effects at import time. Only stdlib + `langchain_core.messages` dependencies.

```python
# utils.py
"""Generic utility functions for JSON serialization and todo parsing."""
import json
import ast
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, AIMessage, ToolMessage


def sanitize_for_json(obj: Any) -> Any:
    """Recursively clean objects for JSON serialization."""
    ...

def safe_json_dumps(obj: Any, ensure_ascii: bool = False) -> str:
    """Safely serialize objects to JSON strings."""
    ...

# --- Todo parsing (private helpers + public API) ---
def _normalize_status(value: Any) -> str: ...
def _extract_bracket_content(text: str, marker: str) -> Optional[str]: ...
def _normalize_todo_items(raw_todos: List[Any]) -> List[Dict[str, Any]]: ...
def _extract_todo_list_from_output(output: Any): ...
def parse_todos_from_tool_output(output: Any): ...
```

### Import Dependency Changes

**app.py line 33** (current):
```python
from tools import tools, sanitize_for_json, safe_json_dumps, parse_todos_from_tool_output
```
Changes to:
```python
from tools import tools
from utils import sanitize_for_json, safe_json_dumps, parse_todos_from_tool_output
```

**knowledge_base.py lines 24-26** (current):
```python
embeddings_model = OllamaEmbeddings(
    model="qwen3-embedding:8b",
    base_url="http://10.108.13.254:11434"
)
```
Changes to:
```python
from config import OLLAMA_BASE_URL, EMBEDDING_MODEL, FAISS_PATH
# ... then use these constants in function parameters
```

**app.py lines 245-246** (current):
```python
max_tokens_before_summary=64000,
messages_to_keep=20,
```
Changes to:
```python
from config import MAX_TOKENS_BEFORE_SUMMARY, MESSAGES_TO_KEEP, RECURSION_LIMIT
# ... then use these in lifespan
```

**tools.py line 35** (current):
```python
db_name = "dcma"
```
Changes to:
```python
from config import DCMA_DB_NAME
# ... use DCMA_DB_NAME in place of "dcma"
```

**subagent/call_api_tool.py line 91** (current):
```python
api_url = "http://10.108.13.250:8001/predict_reason"
```
Changes to:
```python
from config import FAULT_API_URL
# ... use FAULT_API_URL in place of hardcoded URL
```

**app.py line 321** (current):
```python
"recursion_limit": 50
```
Changes to:
```python
"recursion_limit": RECURSION_LIMIT
```

### Anti-Patterns to Avoid
- **Do not create a Config class with `__init__`** -- the user explicitly rejected pydantic-settings; module constants are the right abstraction level
- **Do not add config.py to `.env` loading in tests** -- tests already set environment variables in `conftest.py:_TEST_ENV`; config.py will pick those up automatically
- **Do not move tool functions to utils.py** -- only move generic, non-tool functions. Tool functions stay in `tools.py` (or later `tools/`)
- **Do not refactor tools.py module-level DB connection yet** -- that is Phase 3 (TOOL-02). Phase 2 only changes the `db_name = "dcma"` hardcoded string to use config

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Environment variable loading | Custom .env parser | `python-dotenv` (already installed) | Already used everywhere in the project |
| Type conversion for env vars | Custom type coercion | `int(os.getenv(..., default))` | 2 numeric values only; no framework needed |

**Key insight:** This phase is intentionally simple. The user deferred pydantic-settings validation to v2. The right approach is the simplest one: module-level constants with `os.getenv()` defaults.

## Common Pitfalls

### Pitfall 1: Breaking Test Import Chain
**What goes wrong:** `conftest.py` patches `knowledge_base.init_knowledge_base` and `knowledge_base.create_knowledge_base` before any test imports. If `config.py` triggers a side effect (like connecting to a database) at import time, tests break.
**Why it happens:** `config.py` is imported by `knowledge_base.py`, which is imported by `tools.py`, which is imported by `app.py`. The entire chain runs during `pytest_configure`.
**How to avoid:** `config.py` must only call `load_dotenv()` + `os.getenv()` at module level -- no network calls, no database connections, no file I/O beyond reading `.env`.
**Warning signs:** Tests fail with connection errors or timeouts during collection phase.

### Pitfall 2: Circular Imports
**What goes wrong:** If `utils.py` imports from `tools.py` or `app.py`, circular import occurs.
**Why it happens:** `utils.py` functions currently live in `tools.py`. If the extraction leaves behind a backward reference, Python raises `ImportError`.
**How to avoid:** `utils.py` must only depend on stdlib + `langchain_core.messages`. It must NOT import from `tools.py`, `app.py`, or `config.py`. Verify: `python -c "from utils import sanitize_for_json"` should work in isolation.
**Warning signs:** `ImportError: cannot import name 'X' from partially initialized module`.

### Pitfall 3: Duplicate `load_dotenv()` Calls
**What goes wrong:** Multiple files calling `load_dotenv(override=True)` can mask environment changes or cause confusion about which `.env` file is loaded.
**Why it happens:** Currently `app.py`, `tools.py`, `subagent/call_api_tool.py`, and `knowledge_base.py` all call `load_dotenv()` independently.
**How to avoid:** In Phase 2, centralize the primary `load_dotenv()` call in `config.py`. Other files that import from `config.py` no longer need their own `load_dotenv()` for the config values they read from `config.py`. However, files that still read `.env` directly for their own values (e.g., `sql_inter` reading HOST/USER/MYSQL_PW) should keep their `load_dotenv()` calls until Phase 3 when those are also centralized.
**Warning signs:** Environment variable values differ from `.env` content.

### Pitfall 4: Test Mock Paths Become Stale
**What goes wrong:** Tests mock `knowledge_base.init_knowledge_base` and `knowledge_base.create_knowledge_base`. If `knowledge_base.py` now imports from `config`, the mock must still work correctly.
**Why it happens:** `conftest.py` patches at the module path level. If the import structure changes, patches may target wrong paths.
**How to avoid:** After changes, verify that `knowledge_base.py` still imports correctly with the existing mocks. The key mocks are `knowledge_base.init_knowledge_base` and `knowledge_base.create_knowledge_base` -- these should not need path changes since the module name is not changing.
**Warning signs:** Tests pass individually but fail when run together, or vice versa.

### Pitfall 5: `knowledge_base.py` Auto-Init at Import
**What goes wrong:** `knowledge_base.py` line 134 calls `init_knowledge_base()` at module level. This triggers `create_knowledge_base()` which uses the Ollama URL and FAISS path. After Phase 2, these come from `config.py`. If `config.py` isn't loadable (e.g., dotenv not installed), the whole import chain fails.
**Why it happens:** Module-level side effects combined with new import dependencies.
**How to avoid:** Ensure `config.py` has no dependencies beyond `os` and `python-dotenv` (both always available). The existing `conftest.py` mock on `knowledge_base.init_knowledge_base` prevents actual execution during tests.
**Warning signs:** `ImportError` or `ConnectionError` during `pytest` collection.

## Code Examples

### Complete config.py (recommended implementation)

```python
# config.py
"""Centralized configuration for the fault diagnosis agent system.

All hardcoded values are collected here. Non-secret values have defaults
matching the original hardcoded behavior. Secret values (DB passwords,
API keys) are loaded from .env only.
"""
import os
from dotenv import load_dotenv

load_dotenv(override=True)

# === Knowledge Base (Ollama + FAISS) ===
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://10.108.13.254:11434")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "qwen3-embedding:8b")
FAISS_PATH = os.getenv("FAISS_PATH", "faiss_db")

# === Agent Behavior ===
MAX_TOKENS_BEFORE_SUMMARY = int(os.getenv("MAX_TOKENS_BEFORE_SUMMARY", "64000"))
MESSAGES_TO_KEEP = int(os.getenv("MESSAGES_TO_KEEP", "20"))
RECURSION_LIMIT = int(os.getenv("RECURSION_LIMIT", "50"))

# === Database ===
DCMA_DB_NAME = os.getenv("DCMA_DB_NAME", "dcma")

# === External APIs ===
FAULT_API_URL = os.getenv("FAULT_API_URL", "http://10.108.13.250:8001/predict_reason")
```

### Updating knowledge_base.py to use config

```python
# In knowledge_base.py, replace hardcoded values:
from config import OLLAMA_BASE_URL, EMBEDDING_MODEL, FAISS_PATH

def create_knowledge_base(
        pdf_dir="pdfs",
        db_save_path=None,   # Use FAISS_PATH as default
        force_rebuild=False
):
    if db_save_path is None:
        db_save_path = FAISS_PATH
    # ...
    embeddings_model = OllamaEmbeddings(
        model=EMBEDDING_MODEL,
        base_url=OLLAMA_BASE_URL,
    )
    # ...
```

### Updating app.py import line

```python
# Before (app.py line 33):
from tools import tools, sanitize_for_json, safe_json_dumps, parse_todos_from_tool_output

# After:
from tools import tools
from utils import sanitize_for_json, safe_json_dumps, parse_todos_from_tool_output
from config import MAX_TOKENS_BEFORE_SUMMARY, MESSAGES_TO_KEEP, RECURSION_LIMIT
```

## Hardcoded Value Inventory (Complete)

| # | Value | Current Location | Line(s) | Config Name | Type |
|---|-------|-----------------|---------|-------------|------|
| 1 | `http://10.108.13.254:11434` | knowledge_base.py | 26, 75 | OLLAMA_BASE_URL | str |
| 2 | `qwen3-embedding:8b` | knowledge_base.py | 25, 74 | EMBEDDING_MODEL | str |
| 3 | `faiss_db` | knowledge_base.py | 15 (param default), 16 | FAISS_PATH | str |
| 4 | `64000` | app.py | 245 | MAX_TOKENS_BEFORE_SUMMARY | int |
| 5 | `20` | app.py | 246 | MESSAGES_TO_KEEP | int |
| 6 | `50` | app.py | 321 | RECURSION_LIMIT | int |
| 7 | `dcma` | tools.py | 35 | DCMA_DB_NAME | str |
| 8 | `http://10.108.13.250:8001/predict_reason` | subagent/call_api_tool.py | 91 | FAULT_API_URL | str |

### Occurrences in knowledge_base.py (OLLAMA_BASE_URL and EMBEDDING_MODEL appear twice)

The embedding model and URL are used in **two places** within `knowledge_base.py`:
1. Line 24-26: inside `create_knowledge_base()` when loading an existing FAISS index
2. Line 73-75: inside `create_knowledge_base()` when creating a new FAISS index from PDFs

Both occurrences must be updated to use the config constant.

## Utility Function Inventory (Complete)

Functions to extract from `tools.py` to `utils.py`:

| Function | tools.py Lines | Dependencies | Public API? |
|----------|---------------|--------------|-------------|
| `sanitize_for_json()` | 390-442 | json, datetime, langchain_core.messages | Yes |
| `safe_json_dumps()` | 445-454 | json (calls sanitize_for_json) | Yes |
| `_normalize_status()` | 460-481 | None (pure function) | No (private) |
| `_extract_bracket_content()` | 484-503 | None (pure function) | No (private) |
| `_normalize_todo_items()` | 506-524 | None (pure function) | No (private) |
| `_extract_todo_list_from_output()` | 527-576 | json, ast, re | No (private) |
| `parse_todos_from_tool_output()` | 579-583 | Calls above helpers | Yes |

**Total:** 195 lines (tools.py lines 389-583)
**External dependencies:** `json`, `ast`, `re`, `datetime` (all stdlib) + `langchain_core.messages` (for type checking in `sanitize_for_json`)
**No circular import risk:** None of these functions import from `tools`, `app`, `config`, or any project module.

## Test Impact Analysis

### Current Test Structure (22 tests total)

| File | Test Count | Imports from app/tools | Impact |
|------|-----------|----------------------|--------|
| test_smoke.py | 3 | `from app import app` | LOW: app.py import line changes but functionality unchanged |
| test_sse_stream.py | 6 | Via test_client fixture | LOW: only uses SSE endpoint, not utils directly |
| test_tool_calls.py | 4 | Via test_client fixture | LOW: only uses SSE endpoint, not utils directly |
| test_history_api.py | 6 | Via test_client fixture | LOW: only uses API endpoints |
| helpers.py | 0 (utility) | None | NONE: pure helper functions |
| conftest.py | -- | patches knowledge_base, SQLDatabase, TavilySearch | MEDIUM: see analysis below |

### conftest.py Mock Path Analysis

Current patches in `pytest_configure` (conftest.py lines 98-106):
1. `knowledge_base.init_knowledge_base` -- still valid, module name unchanged
2. `knowledge_base.create_knowledge_base` -- still valid, module name unchanged
3. `langchain_community.utilities.SQLDatabase.from_uri` -- unchanged
4. `langchain_community.agent_toolkits.SQLDatabaseToolkit` -- unchanged
5. `langchain_tavily.TavilySearch` -- unchanged

**New concern:** When `knowledge_base.py` imports from `config.py`, the `config.py` module will run `load_dotenv()` + `os.getenv()` during test collection. This is safe because `conftest.py` sets `_TEST_ENV` with `os.environ.setdefault()` at lines 16-28, which happens in `pytest_configure` BEFORE any production module imports. The `config.py` will see test environment values for any variables set there, and use defaults for new variables (like `OLLAMA_BASE_URL`).

**Verdict:** No mock path changes needed for Phase 2. All 22 tests should pass without conftest.py modifications.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (installed in conda faultagent env) |
| Config file | pytest.ini at project root |
| Quick run command | `pytest tests/ -x -q` |
| Full suite command | `pytest tests/ -v` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| CONF-01 | config.py exists with all 8 values | unit | `pytest tests/test_config.py -x` | Wave 0 |
| CONF-02 | utils.py exists with sanitize_for_json, safe_json_dumps, parse_todos_from_tool_output | unit | `pytest tests/test_utils.py -x` | Wave 0 |
| CONF-03 | app.py/tools.py/knowledge_base.py import from config/utils | integration (existing 22 tests) | `pytest tests/ -x -q` | Existing |

### Sampling Rate
- **Per task commit:** `pytest tests/ -x -q`
- **Per wave merge:** `pytest tests/ -v`
- **Phase gate:** Full suite green (22 existing + new tests) before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_config.py` -- import config.py, verify all 8 constants have expected default values, verify env var override works
- [ ] `tests/test_utils.py` -- import utils.py, verify sanitize_for_json/safe_json_dumps/parse_todos_from_tool_output work correctly with representative inputs (can reuse test cases from the existing code behavior)

## Open Questions

1. **Should `rebuild_kb.py` be updated in Phase 2?**
   - What we know: `rebuild_kb.py` calls `knowledge_base.create_knowledge_base()` directly. After Phase 2, `knowledge_base.py` reads from `config.py` instead of hardcoded values. `rebuild_kb.py` should work without changes since it uses `knowledge_base.py`'s public API.
   - What's unclear: Whether `rebuild_kb.py` passes custom paths that override the config defaults
   - Recommendation: Read `rebuild_kb.py` during implementation; it likely needs no changes since it uses the function API, not the hardcoded values directly. KBAS-03 is a Phase 4 requirement, not Phase 2.

2. **Should `tools.py` keep backward-compatible exports for `sanitize_for_json` etc.?**
   - What we know: `app.py` line 33 is the only consumer that imports these from `tools`. After updating app.py's import, no other file needs them from `tools`.
   - What's unclear: Whether any external consumer (notebook, script) imports from `tools`
   - Recommendation: Remove the functions from `tools.py` cleanly. If anything breaks, it will be caught by the test suite. No backward-compat re-exports needed.

## Sources

### Primary (HIGH confidence)
- Direct code inspection of `app.py` (592 lines), `tools.py` (597 lines), `knowledge_base.py` (134 lines), `subagent/call_api_tool.py` (230 lines)
- Direct inspection of `tests/conftest.py` and all 4 test files (22 tests)
- CONTEXT.md decisions from user discussion session

### Secondary (MEDIUM confidence)
- REQUIREMENTS.md requirement definitions (CONF-01, CONF-02, CONF-03)
- ROADMAP.md phase boundary definitions

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - no new libraries; pure extraction of existing code
- Architecture: HIGH - simple module-level constants pattern, well-understood Python idiom
- Pitfalls: HIGH - full import chain traced, mock paths verified, test impact analyzed line by line

**Research date:** 2026-03-26
**Valid until:** 2026-04-26 (stable; no external dependency changes expected)
