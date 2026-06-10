# External Integrations

**Analysis Date:** 2026-03-26

## APIs & External Services

**LLM / AI Model API (OpenAI-compatible):**
- Service: OpenAI-compatible chat API (configurable provider)
- SDK/Client: `langchain-openai` `ChatOpenAI` class
- Auth: `OPENAI_API_KEY` env var
- Base URL: `OPENAI_BASE_URL` env var
- Model: `MODEL_NAME` env var
- Used in: `app.py` (main model + summary model), `tools.py` (SQL toolkit model), `subagent/fault_explanation_agent.py` (sub-agent model), `subagent/call_api_tool.py` (SQL toolkit model)
- Notes: All four model instances share the same env var configuration. Historical commented-out code shows past usage of DeepSeek, ZhipuAI/GLM, Xiaomi MiMo, and direct OpenAI models.

**Ollama Local LLM Server:**
- Service: Self-hosted Ollama at `http://10.108.13.254:11434`
- SDK/Client: `langchain-ollama` `OllamaEmbeddings`
- Purpose: Text embeddings for FAISS knowledge base (model: `qwen3-embedding:8b`)
- Used in: `knowledge_base.py` lines 24-27 and 73-76
- Auth: None (internal network)
- Notes: `ChatOllama` is imported in `app.py` and `subagent/fault_explanation_agent.py` but currently unused (commented out)

**Fault Diagnosis Prediction API:**
- Service: Custom ML prediction service at `http://10.108.13.250:8001/predict_reason`
- SDK/Client: `requests` library (sync HTTP POST)
- Purpose: SHAP-based fault diagnosis on 36-channel sensor time series data
- Used in: `subagent/call_api_tool.py` lines 89-155
- Auth: None (internal network)
- Input: JSON `{"sequence": [[36 floats], ...]}` (up to 512 rows of sensor data)
- Output: JSON with `prediction`, `label`, `probability`, `channel_importance`, `time_window`
- Timeout: 15 seconds

**Tavily Web Search:**
- Service: Tavily search API
- SDK/Client: `langchain-tavily` `TavilySearch`
- Purpose: Supplementary web search when knowledge base is insufficient
- Used in: `tools.py` line 27
- Auth: Requires `TAVILY_API_KEY` env var (implicit via LangChain)
- Config: `max_results=5`, `topic="general"`

## Data Storage

**MySQL (Primary Sensor Database):**
- Provider: MySQL server at `10.108.12.164:3306`
- Connection: `mysql+pymysql://{USER}:{MYSQL_PW}@{HOST}:{PORT}/{DB_NAME}`
- Client Libraries:
  - `pymysql` - Direct cursor queries in `tools.py` (`sql_inter` tool) and `subagent/call_api_tool.py`
  - `sqlalchemy` - Engine-based queries in `app.py` (`extract_data` tool)
  - `langchain_community.utilities.SQLDatabase` - Schema introspection and SQL toolkit in `tools.py` and `subagent/call_api_tool.py`
- Databases:
  - `agent` (via `DB_NAME` env var) - Mechanical arm sensor data tables
  - `dcma` (hardcoded in `tools.py` line 35) - DCMA system data
- Table naming: `data_{device}_{fault_type}_{location}` (e.g., `data_J3_chainloose_LDF0`)
- Env vars: `HOST`, `USER`, `MYSQL_PW`, `DB_NAME`, `PORT`

**PostgreSQL (Conversation State):**
- Provider: PostgreSQL at `10.108.13.254:5434`
- Purpose: LangGraph checkpoint persistence for conversation history and state
- Client: `psycopg` via `AsyncConnectionPool` (min 2, max 10 connections)
- Implementation: `AsyncPostgresSaver` from `langgraph-checkpoint-postgres`
- Used in: `app.py` lines 239-252 (lifespan context manager)
- Env vars: `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`
- Connection URI: `postgresql://{user}:{password}@{host}:{port}/{db}`

**FAISS Vector Database (Knowledge Base):**
- Type: Local file-based vector store
- Library: `faiss-cpu` 1.13.1 via `langchain_community.vectorstores.FAISS`
- Purpose: Semantic search over PDF documents (fault manuals)
- Storage: `faiss_db/` directory (gitignored, auto-generated)
- Source documents: `pdfs/` directory (currently contains `数据库简介.pdf` and `S120_故障手册.pdf`)
- Embedding model: `qwen3-embedding:8b` via Ollama
- Chunk config: `RecursiveCharacterTextSplitter` with `chunk_size=3000`, `chunk_overlap=1000`
- Used in: `knowledge_base.py`
- Rebuild: `python rebuild_kb.py`

**File Storage:**
- Local filesystem only
- Generated images: `agent_fronted/public/images/` (matplotlib PNG charts)
- Generated reports: `agent_fronted/public/reports/` (Markdown `.md` and HTML `.html` files)
- Served via FastAPI static file mounts at `/images/` and `/reports/`

**Caching:**
- `redis` 7.1.0 is listed in `requirements.txt` but has no detected usage in the codebase
- No caching layer currently in use

## Authentication & Identity

**Auth Provider:** Custom (minimal)
- Implementation: Simple string-based identity (`"游客"` guest / `"管理员"` admin)
- Passed as query parameter `user_identity` on SSE endpoint
- Used to select system prompt variation in `prompt_template.py` `get_identity_system_prompt()`
- No login, no tokens, no session management
- Default identity: `"游客"` (guest)
- Frontend store: `agent_fronted/src/stores/userIdentity.ts`

**API Security:**
- CORS: Wide open (`allow_origins=["*"]`) in `app.py` line 303-309
- No API key or authentication on any endpoint
- Designed for internal network deployment only

## Monitoring & Observability

**Error Tracking:**
- None (no Sentry, Datadog, etc.)
- Errors printed to stdout with `print()` statements

**Logs:**
- Console-based logging via `print()` throughout the codebase
- Uses emoji prefixes for visual scanning (e.g., `"✅"`, `"❌"`, `"⚠️"`, `"🚀"`)
- Production: stdout redirected to `app.log` via `nohup`
- No structured logging framework

## CI/CD & Deployment

**Hosting:**
- Internal network server (target directory: `/opt/agent/`)
- No cloud hosting

**CI Pipeline:**
- None detected - No GitHub Actions, GitLab CI, or other CI configuration

**Deployment Method:**
- Manual deployment via SCP/file copy
- Steps documented in `DEPLOY.md`
- Conda environment setup, pip install, npm build, gunicorn start
- Optional Nginx reverse proxy

## Environment Configuration

**Required env vars:**

| Variable | Purpose | Used In |
|----------|---------|---------|
| `HOST` | MySQL hostname | `tools.py`, `app.py`, `subagent/call_api_tool.py` |
| `USER` | MySQL username | `tools.py`, `app.py`, `subagent/call_api_tool.py` |
| `MYSQL_PW` | MySQL password | `tools.py`, `app.py`, `subagent/call_api_tool.py` |
| `DB_NAME` | MySQL database name (mechanical arm data) | `tools.py`, `app.py`, `subagent/call_api_tool.py` |
| `PORT` | MySQL port | `tools.py`, `app.py`, `subagent/call_api_tool.py` |
| `POSTGRES_HOST` | PostgreSQL hostname | `app.py` |
| `POSTGRES_PORT` | PostgreSQL port | `app.py` |
| `POSTGRES_DB` | PostgreSQL database name | `app.py` |
| `POSTGRES_USER` | PostgreSQL username | `app.py` |
| `POSTGRES_PASSWORD` | PostgreSQL password | `app.py` |
| `MODEL_NAME` | LLM model identifier | `app.py`, `tools.py`, `subagent/call_api_tool.py`, `subagent/fault_explanation_agent.py` |
| `OPENAI_BASE_URL` | LLM API base URL | `app.py`, `tools.py`, `subagent/call_api_tool.py`, `subagent/fault_explanation_agent.py` |
| `OPENAI_API_KEY` | LLM API authentication key | `app.py`, `tools.py`, `subagent/call_api_tool.py`, `subagent/fault_explanation_agent.py` |
| `TAVILY_API_KEY` | Tavily search API key (implicit) | `tools.py` (via LangChain) |

**Secrets location:**
- `.env` file at project root (gitignored)
- No secrets manager or vault integration

## Webhooks & Callbacks

**Incoming:**
- None - No webhook endpoints

**Outgoing:**
- None - No outgoing webhook calls

## Communication Protocols

**Frontend-to-Backend:**
- SSE (Server-Sent Events) via native `EventSource` for streaming chat
- Endpoint: `GET /chat/stream?message=...&thread_id=...&user_identity=...`
- Event types: `start`, `token`, `tool_start`, `tool_end`, `complete`, `server_error`
- REST JSON for chat history (`GET /ai/history/{type}`) and todos (`GET /api/todos/{thread_id}`)

**Backend-to-External:**
- Sync HTTP POST to fault diagnosis API (`requests` library, `subagent/call_api_tool.py`)
- OpenAI-compatible chat completions API (via `langchain-openai`, streaming)
- Ollama REST API for embeddings (via `langchain-ollama`)
- MySQL wire protocol (via `pymysql` and `sqlalchemy`)
- PostgreSQL wire protocol (via `psycopg` async with connection pooling)

## Third-Party CDN Resources

**HTML Reports:**
- ECharts 5.4.3 loaded from jsDelivr CDN in `html_template.html` line 7
- Used for interactive charts in generated HTML reports

---

*Integration audit: 2026-03-26*
