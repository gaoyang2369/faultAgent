# Coding Conventions

**Analysis Date:** 2026-03-26

## Naming Conventions

**Files (Python backend):**
- Use `snake_case` for all Python file names: `knowledge_base.py`, `prompt_template.py`, `call_api_tool.py`
- Entry point: `app.py` (with a stale copy `app_copy.py` at root)
- Subagent files live in `subagent/` directory without `__init__.py`
- System prompt files use descriptive names ending in `_system_prompt.py`

**Files (TypeScript/Vue frontend):**
- Vue components: `PascalCase.vue` (`ChatMessage.vue`, `TaskPanel.vue`, `ChatSidebar.vue`)
- Composables: `camelCase.ts` prefixed with `use` (`useChatStream.ts`, `useTodosPanel.ts`)
- Stores: `camelCase.ts` (`userIdentity.ts`, `counter.ts`)
- Type definitions: `interface.ts` in `type/` directory
- Services: `api.js` (plain JavaScript, not TypeScript)
- Config: `questionTemplates.ts` in `config/`
- Utils: `identityUtils.ts` in `utils/`

**Functions (Python):**
- Use `snake_case`: `create_knowledge_base()`, `token_stream_events()`, `safe_json_dumps()`
- Private helpers prefixed with underscore: `_normalize_status()`, `_extract_bracket_content()`
- Tool functions use descriptive snake_case: `query_knowledge_base()`, `sql_inter()`, `fig_inter()`

**Functions (TypeScript):**
- Use `camelCase`: `sendMessage()`, `loadChat()`, `resolveUserIdentity()`
- Composables export a single function prefixed with `use`: `useChatStream()`, `useTodosPanel()`
- Event handlers use `on` prefix in callback options: `onMessage`, `onToolCall`, `onComplete`, `onError`

**Classes (Python):**
- Pydantic schemas: `PascalCase` + `Schema` suffix: `KnowledgeBaseQuerySchema`, `FaultAnalysisSchema`, `SaveReportSchema`
- Exception: Some schemas use `Input` suffix: `PythonCodeInput`, `FigCodeInput`
- Dataclasses: `PascalCase`: `Context`

**Variables (Python):**
- Use `snake_case`: `db_retriever`, `current_todos`, `accumulated_content`
- DataFrame naming convention: `df_{table_name}` or `df_{purpose}` (e.g., `df_fault_data`)
- Environment variables loaded via `os.getenv()`: `HOST`, `USER`, `MYSQL_PW`, `DB_NAME`, `PORT`, `MODEL_NAME`

**Variables (TypeScript):**
- Use `camelCase` for reactive refs: `currentMessages`, `isStreaming`, `taskPanelVisible`
- Use `UPPER_SNAKE_CASE` for constants: `BASE_URL`, `STATUS_MAP`
- Computed properties: `camelCase`: `hasTodos`, `pendingCount`, `taskProgressPercent`

**Constants:**
- Python: No formal constant convention; module-level variables used as constants (e.g., `systemprompt`, `FAULT_EXPLANATION_SYSTEM_PROMPT`)
- TypeScript: `UPPER_SNAKE_CASE` for object maps: `STATUS_MAP`

## Code Style

**Indentation:**
- Python: 4 spaces (standard)
- TypeScript/Vue: 2 spaces
- Note: `prompt_template.py` has an inconsistency at `get_identity_system_prompt()` which uses 3-space indentation

**Quotes:**
- Python: Double quotes for strings, single quotes also used inconsistently
- TypeScript: Single quotes for imports, mixed elsewhere

**Semicolons:**
- TypeScript: No trailing semicolons in most `.ts` files (composables, stores)
- `api.js`: Uses semicolons

**Line Length:**
- No enforced limit; some lines in `app.py` and `tools.py` exceed 120 characters

**Trailing Whitespace:**
- No automated trimming; some files have trailing blank lines

**Import Organization (Python):**
1. Standard library imports (`os`, `asyncio`, `json`, `re`, `ast`)
2. Third-party imports (`uvicorn`, `fastapi`, `langchain_*`, `pydantic`, `pandas`)
3. Local imports (`from tools import ...`, `from prompt_template import ...`)
- No blank line separation between groups (inconsistent)
- `load_dotenv(override=True)` called at module level immediately after imports

**Import Organization (TypeScript):**
1. Vue/framework imports (`import { ref } from 'vue'`)
2. Third-party imports (`import { defineStore } from 'pinia'`)
3. Local imports using `@/` alias (`import { chatAPI } from '@/services/api'`)
- Path alias `@` resolves to `agent_fronted/src/` (configured in `agent_fronted/vite.config.ts` and `agent_fronted/tsconfig.app.json`)

**Comment Style:**
- Python: Chinese comments throughout, using `#` for inline and `"""` for docstrings
- Section dividers: `# ===== 标题 =====` pattern used in `app.py`, `tools.py`
- TypeScript: Chinese comments for user-facing logic, English for framework boilerplate
- JSDoc-style `/** */` used sparingly in `agent_fronted/src/utils/identityUtils.ts`

## Language & Localization

**Primary Language:**
- Comments, docstrings, and variable descriptions: **Chinese (Simplified)**
- All user-facing strings in both backend and frontend are Chinese
- Error messages returned from tools use Chinese: `"❌ 执行失败："`, `"✅ 成功创建..."`
- System prompts: entirely in Chinese (`prompt_template.py`, `fault_explanation_system_prompt.py`)

**Emoji Usage:**
- Emoji prefixes in status messages: `✅` (success), `❌` (error), `⚠️` (warning), `🔧` (tool), `🚀` (startup)
- Used in `print()` statements, tool return values, and report templates

**i18n Patterns:**
- Frontend uses `element-plus` with `zh-cn` locale: `agent_fronted/src/main.ts` line 7
- No formal i18n framework; all strings are hardcoded in Chinese
- User identity values are Chinese strings: `"游客"` (tourist), `"管理员"` (admin)

## Patterns in Use

### Pydantic Schema + @tool Decorator Pattern
- **Where**: `tools.py`, `app.py`, `subagent/call_api_tool.py`
- **How**: Every LangChain tool follows this pattern:
  1. Define a Pydantic `BaseModel` subclass with `Field` descriptions (in Chinese)
  2. Decorate a function with `@tool(args_schema=SchemaClass)`
  3. Tool docstring provides detailed usage instructions in Chinese
  4. Return a string with emoji-prefixed status (`✅`/`❌`/`⚠️`)
```python
class KnowledgeBaseQuerySchema(BaseModel):
    query: str = Field(description="检索查询字符串，需明确具体")

@tool(args_schema=KnowledgeBaseQuerySchema)
def query_knowledge_base(query: str) -> str:
    """查询本地知识库获取元信息（故障代码含义）。..."""
    # implementation
```

### FastAPI Lifespan Pattern
- **Where**: `app.py` lines 236-294
- **How**: Uses `@asynccontextmanager` to manage PostgreSQL connection pool and agent initialization at startup, cleanup on shutdown. Agent and pool stored in `app.state`.

### SSE Streaming Pattern
- **Where**: `app.py` (function `token_stream_events`), `agent_fronted/src/services/api.js`
- **How**: Backend yields SSE events (`event: token`, `event: tool_start`, `event: tool_end`, `event: complete`, `event: server_error`). Frontend uses native `EventSource` API with named event listeners.

### Composable Pattern (Vue 3)
- **Where**: `agent_fronted/src/composables/useChatStream.ts`, `agent_fronted/src/composables/useTodosPanel.ts`
- **How**: Export a single function that accepts options/refs and returns reactive state + methods. Follows Vue 3 Composition API conventions.

### Global State Mutation via exec/globals
- **Where**: `app.py` (`python_inter`, `fig_inter`, `extract_data`), `subagent/call_api_tool.py`
- **How**: Tools that execute user-provided code use `exec(py_code, globals(), local_vars)` and `globals()[df_name] = df` to share state between tool invocations. This is intentional for the code execution sandbox.

### Dynamic Prompt Middleware
- **Where**: `app.py` lines 47-57
- **How**: Uses `@dynamic_prompt` decorator from LangChain to inject role-based system prompts at runtime based on `Context.user_identity`.

### Sub-Agent Pattern
- **Where**: `subagent/fault_explanation_agent.py`, `tools.py` (function `fault_explanation_tool`)
- **How**: The main agent delegates to a sub-agent via a tool. The sub-agent is created fresh per invocation with its own model, tools, and system prompt.

## Anti-Patterns Observed

- **Duplicate code across `app.py` and `app_copy.py`**: `app_copy.py` is a stale copy of `app.py` with hardcoded API keys and an older tool set. It creates confusion about which is the production entry point. Files: `app.py`, `app_copy.py`.

- **Hardcoded API keys in `app_copy.py` and `subagent/fault_explanation_agent.py`**: Commented-out lines contain plaintext API keys and secret tokens. Files: `app_copy.py` lines 219-225, `subagent/fault_explanation_agent.py` lines 16-33.

- **Missing `import os` in `subagent/fault_explanation_agent.py`**: The function `create_fault_explanation_agent()` calls `os.getenv()` but `os` is not imported. This will raise `NameError` at runtime. File: `subagent/fault_explanation_agent.py` line 35.

- **Repeated `load_dotenv()` calls**: `load_dotenv(override=True)` is called multiple times at module level and inside functions (`tools.py` lines 22, 31; inside `sql_inter` line 101; inside `extract_data` line 112). File: `tools.py`.

- **SQL injection vulnerability**: `subagent/call_api_tool.py` constructs SQL queries using f-string interpolation with user input (`table_name`, `start_time`, `end_time`). File: `subagent/call_api_tool.py` lines 109-120.

- **`exec()` / `eval()` on user-provided code**: `python_inter` and `fig_inter` tools execute arbitrary Python code. While intentional for the agent architecture, there are no sandboxing or resource limits. Files: `app.py` lines 77-92, lines 177-181.

- **Mixed JS/TS in frontend**: The API service layer is plain JavaScript (`api.js`) while the rest of the frontend is TypeScript. This breaks type safety at the API boundary. File: `agent_fronted/src/services/api.js`.

- **No `__init__.py` in `subagent/`**: The `subagent` directory lacks `__init__.py`, relying on implicit namespace packages. File: `subagent/`.

- **Inconsistent indentation**: `prompt_template.py` function `get_identity_system_prompt` uses 3-space indentation instead of 4. File: `prompt_template.py` lines 83-87.

## Documentation Style

**Docstrings (Python):**
- All tool functions have extensive Chinese docstrings describing usage scenarios, parameters, constraints, and examples
- Docstrings use markdown-like formatting inside triple quotes (tables, bullet lists, code blocks)
- Non-tool functions have brief Chinese docstrings: `"""应用生命周期管理"""`
- Helper functions often lack docstrings entirely

**Comments (Python):**
- Section markers: `# ===== 标题 =====`
- Inline comments in Chinese explaining logic
- Commented-out code blocks retained with old API configurations

**JSDoc (TypeScript):**
- Used in `agent_fronted/src/utils/identityUtils.ts` with `@param` and `@returns` tags
- Not used in composables or stores

**No external documentation tooling:**
- No Sphinx, MkDoc, or other documentation generators
- `README.md` and `DEPLOY.md` provide project-level documentation
- No auto-generated API docs (FastAPI's built-in `/docs` is available but not customized)

---

*Convention analysis: 2026-03-26*
