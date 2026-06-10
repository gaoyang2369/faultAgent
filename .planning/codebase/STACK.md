# Technology Stack

**Analysis Date:** 2026-03-26

## Languages

**Primary:**
- Python 3.12+ - Backend API, AI agent logic, data processing, knowledge base
- TypeScript / Vue 3 SFC - Frontend chat UI

**Secondary:**
- JavaScript (ES modules) - Frontend service layer (`agent_fronted/src/services/api.js`)
- SQL - Database queries executed via tools at runtime
- HTML/CSS - Report templates (`html_template.html`, `md_template.md`)

## Runtime

**Environment:**
- Python 3.12.x (Conda environment `faultagent`, per `DEPLOY.md`)
- Node.js 16+ (for frontend build tooling)

**Package Manager:**
- pip (Python) - `requirements.txt` at project root
- npm (Node.js) - `agent_fronted/package.json`, lockfile present at `agent_fronted/package-lock.json`

## Frameworks

**Core:**
- FastAPI 0.121.0 - HTTP API server (`app.py`)
- Uvicorn 0.38.0 - ASGI server, runs on port 8000
- LangChain 1.0.3 - AI agent framework, tool orchestration
- LangGraph 1.0.5 - Agent graph execution, state management, checkpointing
- Vue 3.5.22 - Frontend SPA framework (`agent_fronted/`)
- Vite 7.1.7 - Frontend build/dev server, runs on port 9005

**AI/ML:**
- LangChain OpenAI 1.0.2 - LLM client (OpenAI-compatible API) (`app.py`, `tools.py`, `subagent/fault_explanation_agent.py`)
- LangChain Ollama 1.0.0 - Local LLM/embedding client (Ollama server)
- LangChain Community 0.4.1 - SQL toolkit, FAISS vector store, PDF loader
- LangChain Tavily 0.2.15 - Web search tool (`tools.py`)
- FAISS CPU 1.13.1 - Local vector database for knowledge base (`knowledge_base.py`)

**Data Science:**
- pandas 2.3.3 - DataFrame manipulation, SQL result handling
- numpy 2.3.4 - Numerical computation
- scipy 1.16.3 - Scientific computing
- scikit-learn 1.7.2 - Machine learning utilities
- matplotlib 3.10.7 - Chart generation (Agg backend, saved as PNG)
- seaborn 0.13.2 - Statistical data visualization

**Testing:**
- Not detected - No test framework or test files found in the project

**Build/Dev:**
- Vite 7.1.7 - Frontend dev server and production bundler (`agent_fronted/vite.config.ts`)
- vue-tsc 3.1.0 - TypeScript checking for Vue SFCs
- sass-embedded 1.93.2 - SCSS/Sass compilation

## Key Dependencies

**Critical:**
- `langchain` 1.0.3 - Core agent creation via `create_agent()`, middleware system (TodoList, SummarizationMiddleware, dynamic_prompt)
- `langgraph` 1.0.5 - Agent execution graph, async streaming (`astream_events`), checkpoint persistence
- `langgraph-checkpoint-postgres` 3.0.3 - PostgreSQL-backed conversation state persistence (`AsyncPostgresSaver`)
- `langchain-openai` 1.0.2 - `ChatOpenAI` client used for both main agent and summary model
- `langchain-ollama` 1.0.0 - `OllamaEmbeddings` for knowledge base vector embeddings (model: `qwen3-embedding:8b`)
- `fastapi` 0.121.0 - HTTP server with SSE streaming support
- `sse-starlette` 2.1.3 - Server-Sent Events for token-level streaming

**Infrastructure:**
- `psycopg` 3.3.2 + `psycopg-pool` 3.3.0 - Async PostgreSQL connection pooling
- `pymysql` 1.1.2 - MySQL database client (sensor data queries)
- `sqlalchemy` 2.0.44 - ORM engine for MySQL via `mysql+pymysql://` URI
- `redis` 7.1.0 - Listed in requirements but no usage detected in codebase
- `aiomysql` 0.3.2 - Listed in requirements but no usage detected in codebase
- `aiosqlite` 0.22.1 - Listed in requirements but no usage detected in codebase

**Frontend Critical:**
- `vue` 3.5.22 - Core SPA framework
- `element-plus` 2.11.4 - UI component library (with Chinese locale `zh-cn`)
- `pinia` 3.0.3 - State management (`agent_fronted/src/stores/`)
- `marked` 16.4.0 - Markdown rendering (for AI message display)
- `highlight.js` 11.11.1 - Code syntax highlighting
- `chart.js` 4.5.0 - Client-side charting
- `dompurify` 3.2.7 - HTML sanitization for rendered markdown

**Document Processing:**
- `pypdf` 6.4.1 - PDF document loading for knowledge base
- `openpyxl` 3.1.5 - Excel file handling
- `weasyprint` 66.0 - HTML-to-PDF conversion
- `markdown` 3.10 / `markdown2` 2.5.4 - Markdown processing
- `docxtemplater` 3.66.7 (frontend) - Word document generation
- `jszip` 3.10.1 / `pizzip` 3.2.0 (frontend) - ZIP file handling for DOCX
- `file-saver` 2.0.5 (frontend) - Client-side file downloads

**Utility:**
- `python-dotenv` 1.2.1 - Environment variable loading from `.env`
- `pydantic` 2.12.3 + `pydantic-settings` 2.11.0 - Data validation, tool argument schemas
- `tenacity` 9.1.2 - Retry logic (imported but usage not confirmed in main code)
- `httpx` 0.28.1 - Async HTTP client (listed, direct usage in `requests` instead)
- `requests` 2.32.5 - Sync HTTP client for fault diagnosis API calls (`subagent/call_api_tool.py`)

## Configuration

**Environment:**
- `.env` file at project root - Contains all runtime configuration (DB credentials, API keys, model settings)
- `python-dotenv` loads `.env` with `load_dotenv(override=True)` at module import time in multiple files
- Key env var groups: MySQL connection, PostgreSQL connection, OpenAI-compatible API, Ollama

**Build:**
- `agent_fronted/vite.config.ts` - Frontend build config with dev proxy to backend port 8000
- `agent_fronted/tsconfig.json` - References `tsconfig.app.json` and `tsconfig.node.json`
- `agent_fronted/tsconfig.app.json` - App-level TypeScript config
- Path alias: `@` maps to `agent_fronted/src/`

## Platform Requirements

**Development:**
- Python 3.12+ via Conda
- Node.js 16+ with npm
- Access to MySQL server (sensor data)
- Access to PostgreSQL server (conversation state)
- Access to Ollama server at `http://10.108.13.254:11434` (embeddings model: `qwen3-embedding:8b`)
- Access to OpenAI-compatible API endpoint (configurable via env vars)
- Access to fault diagnosis API at `http://10.108.13.250:8001/predict_reason`

**Production:**
- Deployed to internal network server at `/opt/agent/`
- Gunicorn with UvicornWorker (4 workers recommended)
- Optional Nginx reverse proxy for static file serving and SSE
- Frontend built with `npm run build` and served as static files by FastAPI (`app.mount("/", StaticFiles(...))`)
- Backend port: 8000, Frontend dev port: 9005

## Key Configuration Files

| File | Purpose |
|------|---------|
| `requirements.txt` | Python dependencies (pip) |
| `agent_fronted/package.json` | Frontend Node.js dependencies |
| `agent_fronted/vite.config.ts` | Vite build config, dev proxy rules |
| `agent_fronted/tsconfig.json` | TypeScript project references |
| `agent_fronted/tsconfig.app.json` | App TypeScript compilation settings |
| `agent_fronted/tsconfig.node.json` | Node/Vite TypeScript settings |
| `.env` | All runtime environment variables (secrets - never read contents) |
| `.gitignore` | Ignores `__pycache__/`, `node_modules/`, `.env`, `faiss_db/`, `pdfs/`, `.idea/`, `dist/` |
| `html_template.html` | HTML report template with ECharts CDN and placeholder tokens |
| `md_template.md` | Markdown report template |
| `DEPLOY.md` | Deployment guide for internal network servers |

---

*Stack analysis: 2026-03-26*
