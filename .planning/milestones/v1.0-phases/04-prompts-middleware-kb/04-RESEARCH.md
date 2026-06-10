# Phase 4: Prompts, Middleware & KB - Research

**Researched:** 2026-03-26
**Domain:** Python module extraction (prompts, middleware assembly, knowledge base configuration)
**Confidence:** HIGH

## Summary

Phase 4 is a pure refactoring extraction: move prompt definitions and dynamic prompt logic from `app.py` + `prompt_template.py` into a new `prompts/` package, extract middleware assembly into `middleware.py`, and eliminate hardcoded values from `knowledge_base.py` by reading from `config.py`.

The codebase is well-positioned for this work. Phase 2 already created `config.py` with `OLLAMA_BASE_URL`, `EMBEDDING_MODEL`, `FAISS_PATH`, `MAX_TOKENS_BEFORE_SUMMARY`, and `MESSAGES_TO_KEEP`. Phase 3 completed tools modularization with clean import patterns. The main risk is import path changes breaking existing test mock patches in `conftest.py`, but analysis confirms the mocks operate at the `langchain.agents.middleware` and `knowledge_base` module levels, which remain unaffected by this phase's changes.

**Primary recommendation:** Execute as three independent waves: (1) `prompts/` package creation + `prompt_template.py` deletion, (2) `middleware.py` extraction, (3) knowledge base configuration. Each wave is independently testable with `pytest tests/ -x`.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **prompts/ 目录结构**: `prompts/__init__.py`, `prompts/system_prompt.py` (systemprompt + get_identity_system_prompt), `prompts/dynamic_prompt.py` (Context dataclass + identity_aware_prompt)
- **注释掉的死代码（~70行旧版身份提示词）直接删除**，只保留活跃代码
- **app.py 改为** `from prompts.dynamic_prompt import Context, identity_aware_prompt`
- **middleware.py 边界**: 纯组装函数 `build_middleware(summary_model)` -> 返回中间件列表；不包含 model 创建；不包含 agent 创建
- **middleware.py 从 `prompts.dynamic_prompt` 导入 `identity_aware_prompt`**
- **middleware.py 从 `config` 导入 `MAX_TOKENS_BEFORE_SUMMARY`, `MESSAGES_TO_KEEP`**
- **知识库保留模块级 init_knowledge_base() 调用**：import 时自动初始化
- **新增 config.py 参数**: `KB_CHUNK_SIZE`, `KB_CHUNK_OVERLAP`, `KB_BATCH_SIZE`
- **knowledge_base.py 硬编码值改为从 config.py 导入**
- **rebuild_knowledge_base() 的 db_save_path 默认值改用 FAISS_PATH**
- **prompt_template.py 删除**
- **rebuild_kb.py import 路径不变** (`from knowledge_base import rebuild_knowledge_base`)

### Claude's Discretion
- prompts/__init__.py 的导出内容
- conftest.py 中 mock patch 路径适配（如果 prompt 导入路径变化需要更新 mock）
- knowledge_base.py 内部函数参数的具体传递方式

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| PROM-01 | 创建 prompts/ 目录，system_prompt.py 包含 systemprompt，dynamic_prompt.py 包含 Context + @dynamic_prompt | Direct file moves from `prompt_template.py` (lines 82-87, 91-220) and `app.py` (lines 35-52). Dead code (lines 10-80) deleted. |
| PROM-02 | 创建 middleware.py，中间件列表组装逻辑从 lifespan 中提取 | Extract `app.py:93-113` into `build_middleware(summary_model)` function. Pattern confirmed in CONTEXT.md. |
| PROM-03 | 旧 prompt_template.py 删除 | Only consumer is `app.py:29`. After import path update to `prompts.system_prompt`, file can be safely deleted. |
| KBAS-01 | knowledge_base.py 的 Ollama URL、embedding model、FAISS path 从 config.py 读取 | Already partially done (lines 8, 21, 27-29). Remaining: hardcoded `chunk_size=3000`, `chunk_overlap=1000`, `batch_size=50` at lines 69-70 and 82. |
| KBAS-02 | 保留 8 秒超时保护机制 | Timeout is in `tools/kb_tools.py:26-31` using `concurrent.futures.ThreadPoolExecutor` with 8s timeout. This is application-level, not OllamaEmbeddings-level. No changes needed -- mechanism is preserved as-is. |
| KBAS-03 | rebuild_kb.py 适配新结构后可正常执行 | `rebuild_kb.py` imports from `knowledge_base` module (unchanged path). Only fix: `rebuild_knowledge_base()` default `db_save_path` should use `FAISS_PATH` instead of hardcoded `"faiss_db"`. |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

- **Tech Stack**: LangChain 1.0.3 + LangGraph 1.0.5 + FastAPI 0.121.0 -- do not upgrade
- **API Contract**: Do not change existing HTTP endpoints
- **No new heavy dependencies**
- **All comments, docstrings, user-facing strings in Chinese (Simplified)**
- **Python style**: `snake_case` files/functions, 4-space indent, double quotes preferred
- **GSD Workflow Enforcement**: Work through GSD commands
- **Environment variables loaded via `python-dotenv`** from `.env`
- **config.py style**: Module-level constants, `os.getenv()` with defaults, grouped by comments

## Standard Stack

No new libraries needed. This phase uses only existing project dependencies.

### Core (Already Installed)
| Library | Version | Purpose | Role in Phase |
|---------|---------|---------|---------------|
| langchain | 1.0.3 | Agent framework | `dynamic_prompt`, `ModelRequest`, `TodoListMiddleware`, `SummarizationMiddleware` imports |
| langchain-ollama | N/A | Ollama integration | `OllamaEmbeddings` in knowledge_base.py |
| langchain-community | N/A | FAISS vector store | `FAISS` in knowledge_base.py |
| python-dotenv | N/A | Env loading | Used by config.py |

### No New Dependencies Required

Phase 4 is a pure refactoring extraction. No `pip install` needed.

## Architecture Patterns

### Target Project Structure After Phase 4
```
.
├── app.py                    # Slimmed: imports from prompts/, middleware.py
├── config.py                 # +3 new constants (KB_CHUNK_SIZE, KB_CHUNK_OVERLAP, KB_BATCH_SIZE)
├── middleware.py              # NEW: build_middleware() assembly function
├── prompts/                   # NEW package
│   ├── __init__.py           # Re-exports for convenience
│   ├── system_prompt.py      # systemprompt string + get_identity_system_prompt()
│   └── dynamic_prompt.py     # Context dataclass + identity_aware_prompt (@dynamic_prompt)
├── knowledge_base.py         # Modified: reads chunk/overlap/batch from config.py
├── rebuild_kb.py             # Modified: db_save_path default uses FAISS_PATH
├── tools/                    # Unchanged
├── utils.py                  # Unchanged
└── tests/                    # Mostly unchanged (mock paths unaffected)
```

### Pattern 1: Prompt Module Extraction
**What:** Move prompt content and dynamic prompt logic into separate files within `prompts/` package.
**When to use:** When prompt definitions are large (220 lines of domain-specific content) and pollute the main application file.

```python
# prompts/system_prompt.py
"""系统提示词模板"""

def get_identity_system_prompt(user_identity: str) -> str:
    """根据用户身份生成系统提示词"""
    if user_identity == "游客":
        return "当前用户是**游客用户**，可能对工业设备故障诊断领域不够熟悉。"
    else:
        return "当前用户是**管理员**，具有专业背景。"

systemprompt = """...(full prompt content)..."""
```

```python
# prompts/dynamic_prompt.py
"""动态提示词中间件"""
from dataclasses import dataclass
from langchain.agents.middleware import dynamic_prompt, ModelRequest
from prompts.system_prompt import systemprompt, get_identity_system_prompt

@dataclass
class Context:
    """上下文数据类，用于动态提示词"""
    user_identity: str  # 用户身份：游客/管理员

@dynamic_prompt
def identity_aware_prompt(request: ModelRequest) -> str:
    """根据用户身份和部署环境动态调整系统提示词"""
    user_identity = request.runtime.context.user_identity
    role = get_identity_system_prompt(user_identity)
    base = role + systemprompt
    return base
```

### Pattern 2: Middleware Assembly Function
**What:** A pure function that constructs the middleware list, receiving only the dependencies it needs.
**When to use:** When middleware setup logic is entangled with application lifecycle code.

```python
# middleware.py
"""中间件组装模块"""
from langchain.agents.middleware import TodoListMiddleware, SummarizationMiddleware
from prompts.dynamic_prompt import identity_aware_prompt
from config import MAX_TOKENS_BEFORE_SUMMARY, MESSAGES_TO_KEEP

def build_middleware(summary_model):
    """组装中间件列表，返回给 create_agent 使用"""
    return [
        TodoListMiddleware(),
        identity_aware_prompt,
        SummarizationMiddleware(
            model=summary_model,
            max_tokens_before_summary=MAX_TOKENS_BEFORE_SUMMARY,
            messages_to_keep=MESSAGES_TO_KEEP,
        ),
    ]
```

### Pattern 3: Config-Driven Knowledge Base Parameters
**What:** Replace hardcoded magic numbers with config.py constants that have the same default values.
**When to use:** When numeric parameters should be environment-overridable.

```python
# config.py additions
# === Knowledge Base Build Parameters ===
KB_CHUNK_SIZE = int(os.getenv("KB_CHUNK_SIZE", "3000"))
KB_CHUNK_OVERLAP = int(os.getenv("KB_CHUNK_OVERLAP", "1000"))
KB_BATCH_SIZE = int(os.getenv("KB_BATCH_SIZE", "50"))
```

### Anti-Patterns to Avoid
- **Importing from the old `prompt_template` module**: After migration, all references must use `prompts.system_prompt` or `prompts.dynamic_prompt`. The old file is deleted.
- **Adding model creation to middleware.py**: The `build_middleware()` function receives `summary_model` as a parameter. Model creation stays in `app.py`.
- **Breaking module-level init**: `knowledge_base.py` calls `init_knowledge_base()` at module level (line 137). This must be preserved -- no lazy init refactoring in this phase.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Dynamic prompt injection | Custom middleware class | `@dynamic_prompt` decorator from `langchain.agents.middleware` | Already integrated with LangChain's agent model request pipeline |
| Knowledge base timeout | Custom async timeout wrapper | `concurrent.futures.ThreadPoolExecutor` with `future.result(timeout=8)` | Already implemented in `tools/kb_tools.py`, battle-tested |

## Common Pitfalls

### Pitfall 1: Circular Import Between prompts/ Files
**What goes wrong:** `dynamic_prompt.py` imports from `system_prompt.py`. If `__init__.py` re-exports from both, and `system_prompt.py` somehow imports from the package, circular import occurs.
**Why it happens:** Python package `__init__.py` re-exports can create unexpected cycles.
**How to avoid:** Keep `__init__.py` lightweight with simple imports. `system_prompt.py` must NOT import from `dynamic_prompt.py` or from `prompts` package itself.
**Warning signs:** `ImportError: cannot import name 'X' from partially initialized module`.

### Pitfall 2: conftest.py Mock Patches Break After Import Path Changes
**What goes wrong:** Tests fail because `patch("prompt_template.something")` targets the old module path.
**Why it happens:** Moving code changes the module path that `unittest.mock.patch` needs to target.
**How to avoid:** Current conftest.py does NOT mock `prompt_template` directly -- it mocks at the `langchain.agents.middleware` level (line 53-63) and `knowledge_base` level (line 104-105). These paths are unaffected. However, `app.py` now imports `from prompts.dynamic_prompt import Context, identity_aware_prompt` -- the conftest mock of `dynamic_prompt` decorator (line 60) still operates at the `langchain.agents.middleware` level, so no change is needed.
**Warning signs:** Tests passing locally but `from app import app` failing in test.

### Pitfall 3: Forgetting to Update app.py Import for prompt_template
**What goes wrong:** `app.py` still has `from prompt_template import systemprompt, get_identity_system_prompt` after deleting the file.
**Why it happens:** Missing the import replacement step.
**How to avoid:** After creating `prompts/`, update `app.py:29` to `from prompts.system_prompt import systemprompt, get_identity_system_prompt` (although note: app.py may no longer need these directly if `identity_aware_prompt` in `dynamic_prompt.py` handles everything internally). Verify: `app.py` uses `systemprompt` and `get_identity_system_prompt` only inside `identity_aware_prompt()`. After moving `identity_aware_prompt` to `prompts/dynamic_prompt.py`, app.py no longer needs to import from `system_prompt.py` at all -- it only needs `from prompts.dynamic_prompt import Context, identity_aware_prompt`.
**Warning signs:** `ModuleNotFoundError: No module named 'prompt_template'`.

### Pitfall 4: rebuild_knowledge_base Default Arg Mismatch
**What goes wrong:** `rebuild_knowledge_base(db_save_path="faiss_db")` is called without arguments, saving to the hardcoded path instead of the configured `FAISS_PATH`.
**Why it happens:** Default argument value `"faiss_db"` differs from config `FAISS_PATH` when env var is set.
**How to avoid:** Change function signature to `db_save_path=None` and use `if db_save_path is None: db_save_path = FAISS_PATH` inside the function body (same pattern as `create_knowledge_base`).
**Warning signs:** Knowledge base rebuilt to wrong directory when `FAISS_PATH` env var is set.

### Pitfall 5: middleware.py Importing at Module Level Triggers Side Effects
**What goes wrong:** Importing `middleware.py` triggers `knowledge_base.init_knowledge_base()` through the import chain.
**Why it happens:** `middleware.py` -> `prompts.dynamic_prompt` -> (no chain to knowledge_base). Actually this is not a risk because `middleware.py` only imports from `prompts.dynamic_prompt` and `config`, neither of which imports `knowledge_base`.
**How to avoid:** Verified: no import chain connects `middleware.py` to `knowledge_base.py`. Safe.

## Code Examples

### Example 1: app.py Lifespan After Refactoring

```python
# app.py lifespan (AFTER phase 4 refactoring)
from prompts.dynamic_prompt import Context, identity_aware_prompt
from middleware import build_middleware

# ... model creation unchanged ...

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... PostgreSQL pool setup unchanged ...

    # Middleware assembly via extracted function
    middleware_list = build_middleware(summary_model)

    # Extend tools with SQL tools
    tools.extend(get_sqltools())

    # Create agent (unchanged pattern)
    agent = create_agent(
        model=model,
        tools=tools,
        checkpointer=checkpointer,
        middleware=middleware_list,
        context_schema=Context,
    )
    # ... rest unchanged ...
```

### Example 2: knowledge_base.py After Config Integration

```python
# knowledge_base.py (AFTER phase 4 refactoring)
from config import (
    OLLAMA_BASE_URL, EMBEDDING_MODEL, FAISS_PATH,
    KB_CHUNK_SIZE, KB_CHUNK_OVERLAP, KB_BATCH_SIZE,
)

# ... in create_knowledge_base():
text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=KB_CHUNK_SIZE,       # was: 3000
    chunk_overlap=KB_CHUNK_OVERLAP,  # was: 1000
    length_function=len,
)
# ...
batch_size = KB_BATCH_SIZE  # was: 50


def rebuild_knowledge_base(pdf_dir="pdfs", db_save_path=None):
    """独立的重建函数（重建知识库）"""
    if db_save_path is None:
        db_save_path = FAISS_PATH
    # ... rest unchanged ...
```

### Example 3: prompts/__init__.py Convenience Re-exports

```python
# prompts/__init__.py
"""提示词模块"""
from prompts.system_prompt import systemprompt, get_identity_system_prompt
from prompts.dynamic_prompt import Context, identity_aware_prompt
```

## Import Dependency Analysis

### Before Phase 4
```
app.py
  ├── from prompt_template import systemprompt, get_identity_system_prompt
  ├── from langchain.agents.middleware import TodoListMiddleware, dynamic_prompt, ModelRequest, SummarizationMiddleware
  └── from config import MAX_TOKENS_BEFORE_SUMMARY, MESSAGES_TO_KEEP, RECURSION_LIMIT

prompt_template.py (standalone, no project imports)
knowledge_base.py
  └── from config import OLLAMA_BASE_URL, EMBEDDING_MODEL, FAISS_PATH
```

### After Phase 4
```
app.py
  ├── from prompts.dynamic_prompt import Context, identity_aware_prompt
  ├── from middleware import build_middleware
  └── from config import RECURSION_LIMIT   # Only RECURSION_LIMIT needed directly

prompts/system_prompt.py (standalone, no project imports)
prompts/dynamic_prompt.py
  ├── from prompts.system_prompt import systemprompt, get_identity_system_prompt
  └── from langchain.agents.middleware import dynamic_prompt, ModelRequest

middleware.py
  ├── from langchain.agents.middleware import TodoListMiddleware, SummarizationMiddleware
  ├── from prompts.dynamic_prompt import identity_aware_prompt
  └── from config import MAX_TOKENS_BEFORE_SUMMARY, MESSAGES_TO_KEEP

knowledge_base.py
  └── from config import OLLAMA_BASE_URL, EMBEDDING_MODEL, FAISS_PATH, KB_CHUNK_SIZE, KB_CHUNK_OVERLAP, KB_BATCH_SIZE
```

### Removed Imports from app.py
- `from prompt_template import ...` -- deleted, replaced by `from prompts.dynamic_prompt import ...`
- `from langchain.agents.middleware import TodoListMiddleware, dynamic_prompt, ModelRequest, SummarizationMiddleware` -- no longer needed in app.py (moved to prompts/ and middleware.py)
- `from config import MAX_TOKENS_BEFORE_SUMMARY, MESSAGES_TO_KEEP` -- no longer needed in app.py (moved to middleware.py)
- `from dataclasses import dataclass` -- no longer needed in app.py (Context moved to prompts/)

## KBAS-02 Timeout Analysis

**Requirement:** Preserve 8-second timeout protection mechanism.

**Current implementation** (in `tools/kb_tools.py:25-31`):
```python
timeout_seconds = 8
with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
    future = executor.submit(db_retriever.invoke, query)
    relevant_docs = future.result(timeout=timeout_seconds)
```

**Analysis:**
- The 8-second timeout is an **application-level** timeout wrapping the `db_retriever.invoke()` call.
- It is NOT an OllamaEmbeddings-level timeout.
- OllamaEmbeddings uses httpx under the hood, which has a default 5-second timeout per request.
- `OllamaEmbeddings` supports `client_kwargs={"timeout": httpx.Timeout(...)}` for httpx-level timeout configuration, but this is separate from the application-level 8s timeout in kb_tools.py.
- **Conclusion:** The timeout mechanism lives in `tools/kb_tools.py`, which is NOT modified in Phase 4. KBAS-02 is satisfied by preserving the existing code as-is. No action required.

**Confidence:** HIGH -- verified by source code inspection of both `tools/kb_tools.py` and `OllamaEmbeddings` class.

## Test Impact Analysis

### Mock Patch Paths -- No Changes Needed

| Mock Target in conftest.py | Patch Path | Affected by Phase 4? | Reason |
|---------------------------|------------|----------------------|--------|
| LangChain middleware | `langchain.agents.middleware` (sys.modules injection) | No | Mocked at library level, not app level |
| Knowledge base init | `knowledge_base.init_knowledge_base` | No | Module path `knowledge_base` unchanged |
| Knowledge base create | `knowledge_base.create_knowledge_base` | No | Module path `knowledge_base` unchanged |
| SQLDatabase | `langchain_community.utilities.SQLDatabase.from_uri` | No | Not related to this phase |
| Tavily | `langchain_tavily.TavilySearch` | No | Not related to this phase |

### Test Client (conftest.py:149-151)

```python
with patch("app.AsyncConnectionPool", return_value=mock_pool), \
     patch("app.AsyncPostgresSaver", return_value=mock_checkpointer), \
     patch("app.create_agent", return_value=mock_agent):
```

These patches target `app.create_agent` (the imported name in app.py). After Phase 4, `create_agent` is still imported directly in app.py, so no change needed.

**New import in app.py** (`from middleware import build_middleware`) does not need mocking because `build_middleware` is a pure function with no side effects.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (with pytest-asyncio) |
| Config file | `pytest.ini` |
| Quick run command | `pytest tests/ -x -q` |
| Full suite command | `pytest tests/ -v` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| PROM-01 | prompts/ package importable, system_prompt.py + dynamic_prompt.py contain expected symbols | unit | `pytest tests/test_prompts.py -x` | Wave 0 |
| PROM-02 | middleware.py build_middleware() returns list with 3 elements | unit | `pytest tests/test_middleware.py -x` | Wave 0 |
| PROM-03 | prompt_template.py does not exist | unit | `pytest tests/test_prompts.py::test_old_prompt_template_deleted -x` | Wave 0 |
| KBAS-01 | knowledge_base.py reads chunk_size, chunk_overlap, batch_size from config | unit | `pytest tests/test_config.py -x` (extend existing) | Partial -- config tests exist, need KB-specific assertions |
| KBAS-02 | 8s timeout preserved in kb_tools.py | manual-only | N/A (code is unchanged, verify by inspection) | N/A -- no code change |
| KBAS-03 | rebuild_kb.py imports work, db_save_path defaults to FAISS_PATH | unit | `pytest tests/test_knowledge_base.py -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/ -x -q`
- **Per wave merge:** `pytest tests/ -v`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_prompts.py` -- covers PROM-01, PROM-03 (import checks, symbol existence, old file deleted)
- [ ] `tests/test_middleware.py` -- covers PROM-02 (build_middleware returns correct list)
- [ ] `tests/test_config.py` additions -- covers KBAS-01 (new KB_CHUNK_SIZE, KB_CHUNK_OVERLAP, KB_BATCH_SIZE constants)
- [ ] `tests/test_knowledge_base.py` -- covers KBAS-03 (rebuild_knowledge_base default path uses FAISS_PATH)

## Sources

### Primary (HIGH confidence)
- **Source code inspection** -- `app.py`, `prompt_template.py`, `knowledge_base.py`, `config.py`, `tools/kb_tools.py`, `tests/conftest.py` -- all read and analyzed directly
- **OllamaEmbeddings source** -- inspected via `inspect.getsource()` and `model_fields` -- confirmed `client_kwargs` passes to httpx, timeout is supported but not the same as the 8s application-level timeout
- **httpx.Client** -- default timeout confirmed as `Timeout(timeout=5.0)`

### Secondary (MEDIUM confidence)
- **CONTEXT.md decisions** -- user-locked architecture decisions from `/gsd:discuss-phase`

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new libraries, pure refactoring
- Architecture: HIGH -- all target code is read and analyzed, import graph verified
- Pitfalls: HIGH -- mock paths confirmed unaffected, import chains verified, no circular dependencies

**Research date:** 2026-03-26
**Valid until:** 2026-04-26 (stable -- no external dependency changes expected)
