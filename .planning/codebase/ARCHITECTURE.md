# Architecture

**Analysis Date:** 2026-03-26

## Pattern Overview

**Overall:** Monolithic full-stack application with AI Agent orchestration

This is an industrial equipment fault diagnosis expert system combining a FastAPI backend (Python) with a Vue 3 frontend (TypeScript). The backend hosts a LangChain/LangGraph-based AI agent that orchestrates multiple tools (SQL queries, knowledge base retrieval, data visualization, report generation, and a sub-agent for fault explanation). The frontend is a single-page chat interface that communicates with the backend via Server-Sent Events (SSE) for streaming responses.

**Key Characteristics:**
- Agent-centric architecture: a main LangGraph agent with middleware (dynamic prompts, todo list, context summarization) drives all business logic
- Tool-based extensibility: new capabilities are added as LangChain `@tool` decorated functions
- Sub-agent delegation: a fault explanation sub-agent handles specialized SHAP-based diagnosis via an external ML API
- SSE-based streaming: real-time token-level streaming from backend to frontend
- Dual database: MySQL for business/sensor data, PostgreSQL for LangGraph checkpoint persistence

## Layers

**Presentation Layer (Frontend - Vue 3 SPA):**
- Purpose: Chat UI with SSE streaming, task panel, sidebar, report viewer
- Location: `agent_fronted/src/`
- Contains: Vue components, composables, stores, services, type definitions
- Depends on: Backend REST/SSE endpoints at `http://localhost:8000`
- Used by: End users (tourists / administrators)

**API Layer (FastAPI):**
- Purpose: HTTP endpoints for chat streaming, history retrieval, todo management, and static file serving
- Location: `app.py` (lines 296-592)
- Contains: FastAPI route handlers, SSE event generator, CORS configuration, static file mounts
- Depends on: Agent layer, PostgreSQL checkpointer
- Used by: Frontend via HTTP/SSE

**Agent Orchestration Layer (LangChain/LangGraph):**
- Purpose: AI agent that processes user messages, selects and invokes tools, manages conversation state
- Location: `app.py` (lines 236-294 for agent creation), `prompt_template.py` (system prompt)
- Contains: Agent creation with middleware (TodoListMiddleware, dynamic_prompt, SummarizationMiddleware), context schema, model configuration
- Depends on: Tool layer, LLM (OpenAI-compatible API), PostgreSQL checkpointer
- Used by: API layer via `app.state.agent`

**Tool Layer:**
- Purpose: Concrete capabilities the agent can invoke (SQL, knowledge base, visualization, reports, sub-agent)
- Location: `tools.py` (main tools), `app.py` (lines 60-202 for extract_data, fig_inter, python_inter)
- Contains: LangChain `@tool` functions with Pydantic schemas
- Depends on: MySQL database, FAISS knowledge base, external APIs, filesystem
- Used by: Agent orchestration layer

**Sub-Agent Layer:**
- Purpose: Specialized fault diagnosis using an external ML prediction API with SHAP analysis
- Location: `subagent/`
- Contains: Sub-agent creation (`fault_explanation_agent.py`), API tool (`call_api_tool.py`), system prompt (`fault_explanation_system_prompt.py`)
- Depends on: External fault prediction API at `http://10.108.13.250:8001/predict_reason`, MySQL for sensor data
- Used by: Main agent via `fault_explanation_tool` in `tools.py`

**Knowledge Base Layer:**
- Purpose: PDF document ingestion into FAISS vector store for semantic retrieval
- Location: `knowledge_base.py`
- Contains: PDF loading, text splitting, embedding via Ollama, FAISS index creation/loading
- Depends on: Ollama embedding model (`qwen3-embedding:8b` at `http://10.108.13.254:11434`), PDF files in `pdfs/`
- Used by: `query_knowledge_base` tool in `tools.py`

## Data Flow

**Primary Chat Flow (User Message to Streaming Response):**

1. User types message in `CustomerService.vue`, triggers `sendMessage()` in `useChatStream.ts`
2. Frontend calls `chatAPI.sendServiceMessageStream()` in `agent_fronted/src/services/api.js`, which opens an `EventSource` (SSE) to `GET /chat/stream?message=...&thread_id=...&user_identity=...`
3. `app.py` handler `stream_chat_log_get()` (line 447) creates a `StreamingResponse` wrapping `token_stream_events()` generator
4. `token_stream_events()` (line 311) invokes `app.state.agent.astream_events()` with the user message and thread config
5. LangGraph agent processes through middleware chain: `TodoListMiddleware` -> `identity_aware_prompt` (dynamic system prompt) -> `SummarizationMiddleware` (context compression)
6. Agent selects and invokes tools as needed; each tool call/result generates SSE events (`tool_start`, `tool_end`)
7. LLM response tokens stream as `on_chat_model_stream` events, yielded as SSE `token` events
8. Frontend receives SSE events via `EventSource` listeners, updates `currentMessages` reactively
9. On completion, a `complete` SSE event includes final content and todos; frontend updates state

**Fault Diagnosis Sub-Flow:**

1. Main agent invokes `fault_explanation_tool` (defined in `tools.py`, line 148)
2. Tool creates a sub-agent via `create_fault_explanation_agent()` in `subagent/fault_explanation_agent.py`
3. Sub-agent invokes `query_fault_data_and_call_api` tool in `subagent/call_api_tool.py`
4. Tool queries MySQL for sensor data (36 channels), formats as sequence, calls external ML API at `http://10.108.13.250:8001/predict_reason`
5. API returns prediction, probability, SHAP channel importance, time windows
6. Sub-agent analyzes results and generates SHAP visualization charts using its own `fig_inter` tool
7. Sub-agent returns natural language summary to main agent
8. Main agent incorporates findings into its response, optionally generates additional charts and reports

**Knowledge Base Query Flow:**

1. Agent invokes `query_knowledge_base` tool in `tools.py` (line 54)
2. Tool imports `db_retriever` from `knowledge_base.py` (global variable initialized at module load)
3. `db_retriever.invoke(query)` runs similarity search against FAISS index (with 8-second timeout)
4. Returns top 3 document fragments with page metadata

**Report Generation Flow:**

1. Agent invokes `save_report` (Markdown) or `save_html_report` (HTML) tools in `tools.py`
2. For HTML: loads `html_template.html`, performs string replacement with `{{placeholder}}` tokens
3. Report saved to `agent_fronted/public/reports/` directory
4. Images saved to `agent_fronted/public/images/` directory
5. FastAPI serves these via static file mounts at `/reports` and `/images`
6. Frontend `ChatMessage.vue` detects report links in responses and opens them in an `el-drawer` sidebar

**State Management:**
- **Backend conversation state**: LangGraph checkpointer persists to PostgreSQL via `AsyncPostgresSaver` with `AsyncConnectionPool`. Each conversation identified by `thread_id`.
- **Backend todo state**: Managed by LangChain's `TodoListMiddleware`, stored in checkpoint `channel_values.todos`
- **Frontend state**: Pinia stores (`userIdentity.ts` for user identity/status), Vue refs in composables (`useChatStream.ts`, `useTodosPanel.ts`) for chat messages and todos
- **User identity**: Received via WebSocket from external system (port 3000), stored in Pinia, sent to backend as `user_identity` query parameter

## Key Abstractions

**Agent (LangGraph ReAct Agent):**
- Purpose: Central decision-making entity that processes user requests through a tool-selection loop
- Created in: `app.py` line 264 via `create_agent(model, tools, checkpointer, middleware, context_schema)`
- Pattern: ReAct (Reasoning + Acting) with middleware pipeline

**Tools (LangChain @tool):**
- Purpose: Individual capabilities the agent can invoke
- Examples: `sql_inter` (`tools.py` line 89), `query_knowledge_base` (`tools.py` line 54), `fig_inter` (`app.py` line 141), `save_report` (`tools.py` line 207), `fault_explanation_tool` (`tools.py` line 148)
- Pattern: Each tool has a Pydantic `BaseModel` input schema and a `@tool` decorated function

**Middleware (LangChain Agent Middleware):**
- Purpose: Cross-cutting concerns applied to every agent invocation
- Examples: `TodoListMiddleware` (task planning), `identity_aware_prompt` (dynamic system prompt), `SummarizationMiddleware` (context compression at 64K tokens)
- Pattern: Middleware chain processed before each LLM call

**Composables (Vue 3 Composition API):**
- Purpose: Reusable stateful logic extracted from components
- Examples: `useChatStream` (`agent_fronted/src/composables/useChatStream.ts`), `useTodosPanel` (`agent_fronted/src/composables/useTodosPanel.ts`)
- Pattern: Functions returning refs and methods, consumed by `CustomerService.vue`

## Entry Points

**Backend:**
- Location: `app.py` line 581 (`if __name__ == "__main__"`)
- Triggers: `python app.py` or `gunicorn -w 4 -k uvicorn.workers.UvicornWorker app:app`
- Responsibilities: Starts FastAPI on `0.0.0.0:8000` with uvicorn, initializes PostgreSQL connection pool, creates LangGraph agent, mounts static files

**Frontend:**
- Location: `agent_fronted/src/main.ts`
- Triggers: `npm run dev` (Vite dev server on port 9005) or `npm run build` (static output)
- Responsibilities: Creates Vue app, registers ElementPlus + Pinia + Router, mounts to `#app`

**Knowledge Base Rebuild:**
- Location: `rebuild_kb.py`
- Triggers: `python rebuild_kb.py`
- Responsibilities: Force-rebuilds FAISS index from PDF files in `pdfs/`

**Sub-Agent Standalone Testing:**
- Location: `subagent/fault_explanation_agent.py` line 105 (`if __name__ == "__main__"`)
- Triggers: `python -m subagent.fault_explanation_agent`
- Responsibilities: Interactive CLI for testing the fault explanation sub-agent

## Error Handling

**Strategy:** Defensive try/except at every boundary with user-friendly error messages

**Backend Patterns:**
- SSE stream errors are caught and sent as `server_error` SSE events (`app.py` lines 430-444)
- Recursion limit errors (LangGraph) produce a specific Chinese-language retry message (`app.py` line 440)
- Tool execution errors return error strings prefixed with failure emoji (`tools.py`, `subagent/call_api_tool.py`)
- Database connections use `finally` blocks for cleanup (`tools.py` line 142, `subagent/call_api_tool.py` line 162)
- Knowledge base retrieval has an 8-second timeout via `concurrent.futures` (`tools.py` line 72)

**Frontend Patterns:**
- SSE errors trigger `onError` callback which updates the last assistant message with error text (`agent_fronted/src/composables/useChatStream.ts` lines 238-249)
- API calls wrapped in try/catch with `console.error` logging (`agent_fronted/src/services/api.js`)
- Todo parsing uses multiple fallback strategies (JSON.parse -> ast.literal_eval -> regex extraction) both backend (`tools.py` lines 460-583) and frontend (`agent_fronted/src/composables/useTodosPanel.ts` lines 116-178)

## Cross-Cutting Concerns

**Logging:** `print()` statements throughout backend code with emoji prefixes for status indication. No structured logging framework.

**Validation:** Pydantic `BaseModel` schemas for tool inputs (`tools.py`, `app.py`). Frontend validates `user_identity` parameter to allowed values ("guest"/"admin") at `app.py` line 459.

**Authentication:** No authentication on API endpoints. User identity (tourist/admin) is received from an external WebSocket system and passed as a query parameter. The backend uses it only for prompt customization, not access control.

**CORS:** Wide-open CORS configuration in `app.py` lines 303-309 (`allow_origins=["*"]`).

**Static File Serving:** FastAPI mounts `agent_fronted/public/` as static root, with `/images` and `/reports` sub-mounts (`app.py` lines 556-566). In development, Vite proxies these to the backend (`agent_fronted/vite.config.ts` lines 12-34).

**Environment Configuration:** All secrets and connection details loaded from `.env` via `python-dotenv`. Environment variables accessed via `os.getenv()` throughout. Key variables: `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `MODEL_NAME`, `HOST`, `USER`, `MYSQL_PW`, `DB_NAME`, `PORT`, `POSTGRES_*`.

---

*Architecture analysis: 2026-03-26*
