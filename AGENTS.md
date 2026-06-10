# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Build & Run Commands

### Backend
```bash
conda activate faultagent          # Python 3.12 environment
pip install -r requirements.txt    # Install dependencies
python -m fault_diagnosis.app      # Dev server on :8000
# Production:
gunicorn -w 4 -k uvicorn.workers.UvicornWorker fault_diagnosis.app:app --bind 0.0.0.0:8000
```

### Frontend
```bash
cd agent_fronted
npm install
npm run dev                        # Dev server on :9005
npm run build                      # Production build → dist/
```

### Knowledge Base
```bash
python rebuild_kb.py               # Rebuild FAISS index from pdfs/
python -c "from fault_diagnosis.knowledge_base import init_knowledge_base; init_knowledge_base()"
```

### Tests
```bash
powershell -ExecutionPolicy Bypass -File .\scripts\run_tests.ps1
```

## Architecture

An AI agent system for industrial equipment fault diagnosis. A single LangGraph ReAct agent processes user messages through a middleware pipeline, invokes tools, and streams responses via SSE.

**Data flow:** Frontend (Vue 3) → SSE/REST → FastAPI (`fault_diagnosis/app.py`) → LangGraph Agent → Tools → MySQL/FAISS/External APIs

**Key files:**
- `fault_diagnosis/app.py` — FastAPI 入口、lifespan、SSE 路由、静态文件挂载
- `fault_diagnosis/tools/` — 主系统工具、知识库工具、报告工具和 SQL 工具
- `fault_diagnosis/prompts/` — 系统提示词与动态提示词注入
- `fault_diagnosis/knowledge_base.py` — FAISS 向量库创建、加载和检索
- `fault_diagnosis/robot_arm/subagent/` — 机械臂故障解释子 Agent

**Databases:**
- MySQL — Sensor/business data (queried by tools at runtime)
- PostgreSQL — LangGraph conversation state persistence (`AsyncPostgresSaver`)

**Agent creation pattern:**
```python
agent = create_agent(
    model=model, tools=tools, checkpointer=checkpointer,
    middleware=[TodoListMiddleware(), identity_aware_prompt, SummarizationMiddleware(...)],
    context_schema=Context,
)
```

**SSE event types:** `start`, `token`, `tool_start`, `tool_end`, `complete`, `server_error`

## Key Patterns

- **Tool definition**: Pydantic `BaseModel` schema + `@tool(args_schema=...)` decorator. Docstrings are in Chinese and serve as the LLM's tool description.
- **`@dynamic_prompt`**: Injects role-based system prompts at runtime based on `Context.user_identity` (游客/管理员).
- **`globals()` sharing**: `extract_data`, `python_inter`, and `fig_inter` share state via `globals()` — `extract_data` stores DataFrames that `fig_inter` later reads. This is intentional.
- **Sub-agent as tool**: `fault_explanation_tool` creates a fresh sub-agent per invocation with its own tools and prompt.
- **Frontend path alias**: `@` → `agent_fronted/src/` in imports.

## Language & Style

- **All comments, docstrings, user-facing strings, and system prompts are in Chinese (Simplified)**
- Status prefixes: `✅` success, `❌` error, `⚠️` warning, `🚀` startup
- Python: `snake_case` files/functions, 4-space indent, double quotes preferred
- Vue: `PascalCase.vue` components, `camelCase.ts` composables prefixed with `use`
- Environment variables loaded via `python-dotenv` from `.env` at project root

## Configuration

All runtime config is in `.env` (never read its contents — contains secrets):
- MySQL connection: `HOST`, `USER`, `MYSQL_PW`, `DB_NAME`, `PORT`
- PostgreSQL: `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`
- LLM: `MODEL_NAME`, `OPENAI_BASE_URL`, `OPENAI_API_KEY`
- Ollama embeddings server: hardcoded to `http://10.108.13.254:11434` (model: `qwen3-embedding:8b`)

## Constraints

- **Tech Stack**: LangChain 1.0.3 + LangGraph 1.0.5 + FastAPI 0.121.0 — do not upgrade
- **API Contract**: Do not change existing HTTP endpoints — frontend depends on them unchanged
- **No new heavy dependencies** — keep the project lightweight

## Active Refactoring

This project is being refactored from monolith to modular architecture. See `.planning/ROADMAP.md` for the 7-phase plan. The goal is splitting into `agent_core/` (reusable framework) + `projects/fault_diagnosis/` (domain-specific code).

Phase status tracked in `.planning/STATE.md`. Requirements in `.planning/REQUIREMENTS.md`.

## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd:quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd:debug` for investigation and bug fixing
- `/gsd:execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
