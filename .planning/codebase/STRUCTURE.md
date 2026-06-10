# Codebase Structure

**Analysis Date:** 2026-03-26

## Directory Layout

```
fault-diagnosis/
├── app.py                          # FastAPI backend entry point, agent creation, SSE streaming, API routes
├── app_copy.py                     # Backup copy of app.py (legacy)
├── tools.py                        # Main LangChain tool definitions (SQL, KB, reports, utilities)
├── prompt_template.py              # System prompt templates for identity-aware prompting
├── knowledge_base.py               # FAISS knowledge base creation, loading, and retrieval
├── rebuild_kb.py                   # CLI script to force-rebuild the FAISS knowledge base
├── html_template.html              # HTML report template with ECharts support
├── md_template.md                  # Markdown report template (not actively used)
├── requirements.txt                # Python dependencies
├── .env                            # Environment variables (secrets - DO NOT READ)
├── .gitignore                      # Git ignore rules
├── README.md                       # Project documentation
├── DEPLOY.md                       # Deployment guide for internal servers
├── pdfs/                           # PDF documents for knowledge base ingestion
│   ├── 数据库简介.pdf               # Database introduction document
│   └── S120_故障手册.pdf            # S120 fault manual
├── subagent/                       # Fault explanation sub-agent module
│   ├── call_api_tool.py            # Tool: query sensor data + call ML prediction API + fig_inter
│   ├── fault_explanation_agent.py  # Sub-agent creation and standalone testing
│   ├── fault_explanation_system_prompt.py  # Sub-agent system prompt
│   └── api_style.md               # API style documentation
├── agent_fronted/                  # Vue 3 frontend project
│   ├── package.json                # Frontend dependencies and scripts
│   ├── vite.config.ts              # Vite configuration (port 9005, proxy to backend)
│   ├── tsconfig.json               # TypeScript root config
│   ├── tsconfig.app.json           # TypeScript app config
│   ├── tsconfig.node.json          # TypeScript node config
│   ├── index.html                  # HTML entry point for Vite
│   ├── kill-port.js                # Utility to kill process on dev port before starting
│   ├── README.md                   # Frontend README
│   ├── README.en.md                # Frontend README (English)
│   ├── public/                     # Static assets served directly
│   │   ├── vite.svg                # Vite logo
│   │   ├── images/                 # Generated chart images (written by tools at runtime)
│   │   └── reports/                # Generated report files (written by tools at runtime)
│   └── src/                        # Vue source code
│       ├── main.ts                 # App bootstrap: Vue + ElementPlus + Pinia + Router
│       ├── App.vue                 # Root component: navbar, identity indicator, dark mode toggle
│       ├── assets/                 # Static assets (CSS, SVG)
│       │   ├── base.css            # CSS reset and variables
│       │   ├── main.css            # Global styles
│       │   ├── ChatMessage.css     # Styles for ChatMessage component
│       │   ├── CustomerService.css # Styles for CustomerService view
│       │   ├── default-avatar.svg  # Default user avatar
│       │   ├── machine.svg         # Machine/robot arm role avatar
│       │   ├── flower.svg          # Flower/agriculture role avatar
│       │   └── logo.svg            # App logo
│       ├── components/             # Reusable Vue components
│       │   ├── ChatMessage.vue     # Chat message bubble with markdown, code highlight, image preview, report viewer
│       │   ├── ChatSidebar.vue     # Chat history sidebar
│       │   ├── TaskPanel.vue       # Todo/task progress panel
│       │   ├── PDFViewer.vue       # PDF document viewer component
│       │   ├── WelcomeItem.vue     # Welcome page item (template scaffolding, unused)
│       │   └── icons/              # Icon components (template scaffolding, unused)
│       ├── composables/            # Vue composition API hooks
│       │   ├── useChatStream.ts    # Chat SSE streaming logic, message management
│       │   └── useTodosPanel.ts    # Todo panel state, normalization, API fetch
│       ├── config/                 # Application configuration
│       │   └── questionTemplates.ts  # Pre-defined question templates by user role
│       ├── hooks/                  # Legacy hooks directory
│       │   ├── useVoice.js         # Voice recognition and text-to-speech hook
│       │   └── useVoice.d.ts       # Type declarations for useVoice
│       ├── router/                 # Vue Router configuration
│       │   ├── index.ts            # Router setup (currently empty routes)
│       │   └── index.js            # Legacy JS router (duplicate)
│       ├── services/               # API service layer
│       │   ├── api.js              # Backend API client (REST + SSE via EventSource)
│       │   └── api.d.ts            # Type declarations for api.js
│       ├── stores/                 # Pinia state management
│       │   ├── userIdentity.ts     # User identity store (userId, role, connection status)
│       │   └── counter.ts          # Counter store (template scaffolding, unused)
│       ├── type/                   # TypeScript type definitions
│       │   └── interface.ts        # Shared interfaces (ChartDataItem, message)
│       └── utils/                  # Utility functions
│           ├── identityUtils.ts    # User identity processing, display name generation
│           └── pdfStorage.js       # PDF file storage utilities
└── faiss_db/                       # Generated FAISS vector index (not committed)
    ├── index.faiss                 # FAISS index file
    └── index.pkl                   # FAISS metadata pickle
```

## Directory Purposes

**Root (`/`):**
- Purpose: Backend Python application files and project configuration
- Contains: FastAPI app, tools, prompt templates, knowledge base logic, requirements
- Key files: `app.py`, `tools.py`, `prompt_template.py`, `knowledge_base.py`

**`subagent/`:**
- Purpose: Fault explanation sub-agent with its own tools and system prompt
- Contains: Sub-agent factory, ML API calling tool, SHAP visualization tool, system prompt
- Key files: `subagent/fault_explanation_agent.py`, `subagent/call_api_tool.py`, `subagent/fault_explanation_system_prompt.py`

**`pdfs/`:**
- Purpose: Source PDF documents ingested into the FAISS knowledge base
- Contains: Equipment manuals and reference documents
- Key files: `pdfs/S120_故障手册.pdf`, `pdfs/数据库简介.pdf`

**`agent_fronted/`:**
- Purpose: Vue 3 + TypeScript frontend project
- Contains: Full frontend SPA source, build config, static assets
- Key files: `agent_fronted/package.json`, `agent_fronted/vite.config.ts`

**`agent_fronted/src/components/`:**
- Purpose: Reusable Vue components
- Contains: Chat message rendering, sidebar, task panel, PDF viewer
- Key files: `agent_fronted/src/components/ChatMessage.vue` (832 lines, most complex component)

**`agent_fronted/src/composables/`:**
- Purpose: Vue Composition API composables for stateful logic reuse
- Contains: Chat streaming logic, todo panel management
- Key files: `agent_fronted/src/composables/useChatStream.ts`, `agent_fronted/src/composables/useTodosPanel.ts`

**`agent_fronted/src/stores/`:**
- Purpose: Pinia state stores for global application state
- Contains: User identity management
- Key files: `agent_fronted/src/stores/userIdentity.ts`

**`agent_fronted/src/services/`:**
- Purpose: HTTP/SSE API client layer
- Contains: All backend API calls centralized in one module
- Key files: `agent_fronted/src/services/api.js`

**`agent_fronted/public/`:**
- Purpose: Static files served directly and runtime-generated content
- Contains: Generated chart images and diagnostic reports
- Key files: `agent_fronted/public/images/` (generated), `agent_fronted/public/reports/` (generated)

## Key File Locations

**Entry Points:**
- `app.py`: Backend entry point (FastAPI app, starts uvicorn on port 8000)
- `agent_fronted/src/main.ts`: Frontend entry point (Vue 3 app bootstrap)
- `rebuild_kb.py`: CLI utility to rebuild the FAISS knowledge base

**Configuration:**
- `.env`: Environment variables (database credentials, API keys, model config) -- DO NOT READ
- `agent_fronted/vite.config.ts`: Vite build config, dev server port (9005), proxy rules
- `agent_fronted/tsconfig.json`: TypeScript configuration
- `requirements.txt`: Python dependency pinning

**Core Backend Logic:**
- `app.py` (591 lines): Agent creation, middleware configuration, SSE streaming, API routes
- `tools.py` (596 lines): All LangChain tool definitions (SQL, KB, reports, utilities, todo parsing)
- `prompt_template.py` (219 lines): System prompt with workflow instructions, tool usage guide
- `knowledge_base.py` (133 lines): FAISS vector store creation/loading/retrieval

**Sub-Agent:**
- `subagent/call_api_tool.py` (230 lines): Sensor data query + ML API call + chart generation tool
- `subagent/fault_explanation_agent.py` (109 lines): Sub-agent factory and standalone test CLI
- `subagent/fault_explanation_system_prompt.py` (113 lines): Detailed analysis prompt for fault explanation

**Core Frontend Logic:**
- `agent_fronted/src/views/CustomerService.vue` (376 lines): Main chat view, orchestrates all composables
- `agent_fronted/src/components/ChatMessage.vue` (832 lines): Message rendering with markdown, code highlight, image preview, report viewer drawer
- `agent_fronted/src/composables/useChatStream.ts` (283 lines): SSE streaming, message management, chat CRUD
- `agent_fronted/src/composables/useTodosPanel.ts` (240 lines): Todo state management and API sync
- `agent_fronted/src/services/api.js` (213 lines): Backend API client (REST + SSE)

**Templates:**
- `html_template.html` (247 lines): HTML report template with ECharts auto-initialization
- `md_template.md`: Markdown report template (exists but not actively used by tools)

**Testing:**
- No test files detected in the project

## Naming Conventions

**Backend Files:**
- Pattern: `snake_case.py` for all Python modules
- Examples: `app.py`, `tools.py`, `prompt_template.py`, `knowledge_base.py`

**Frontend Files:**
- Vue components: `PascalCase.vue` (e.g., `ChatMessage.vue`, `TaskPanel.vue`, `CustomerService.vue`)
- Composables: `camelCase.ts` prefixed with `use` (e.g., `useChatStream.ts`, `useTodosPanel.ts`)
- Stores: `camelCase.ts` (e.g., `userIdentity.ts`, `counter.ts`)
- Services: `camelCase.js` (e.g., `api.js`)
- Config: `camelCase.ts` (e.g., `questionTemplates.ts`)
- Utils: `camelCase.ts` or `camelCase.js` (e.g., `identityUtils.ts`, `pdfStorage.js`)
- Type definitions: `camelCase.ts` (e.g., `interface.ts`)

**Directories:**
- Backend: `snake_case` (e.g., `subagent/`)
- Frontend: `camelCase` (e.g., `composables/`, `components/`, `services/`)

## Where to Add New Code

**New LangChain Tool:**
- Define Pydantic schema class + `@tool` function in `tools.py`
- Add to the `tools` list at `tools.py` line 586
- If tool needs sub-agent scope, add to `subagent/call_api_tool.py` line 230
- Update system prompt tool reference in `prompt_template.py`

**New Sub-Agent:**
- Create new directory under `subagent/` or add files to existing `subagent/`
- Create: `subagent/{name}_agent.py` (agent factory), `subagent/{name}_system_prompt.py` (prompt), `subagent/{name}_tools.py` (tools)
- Register a wrapper `@tool` in `tools.py` that creates and invokes the sub-agent

**New API Endpoint:**
- Add route handler in `app.py` BEFORE the static file mount (line 556)
- Static file mounts are catch-all; routes after them will not be reached

**New Vue Component:**
- Create component in `agent_fronted/src/components/`
- Import and use in `agent_fronted/src/views/CustomerService.vue` or create a new view

**New Vue View/Page:**
- Create view in `agent_fronted/src/views/`
- Add route in `agent_fronted/src/router/index.ts` (currently empty, routes would go in the `routes` array)
- Note: The app currently has no routes configured; `CustomerService.vue` is rendered directly via `App.vue`'s `<router-view>` with empty route config, meaning it relies on a default or fallback

**New Composable:**
- Create in `agent_fronted/src/composables/use{Name}.ts`
- Follow the pattern of `useChatStream.ts`: export a function returning refs and methods
- Import in the consuming component

**New Pinia Store:**
- Create in `agent_fronted/src/stores/{storeName}.ts`
- Use `defineStore` with Composition API style (see `userIdentity.ts` for pattern)
- Import via `use{StoreName}Store` convention

**New PDF for Knowledge Base:**
- Place PDF file in `pdfs/` directory
- Run `python rebuild_kb.py` to regenerate the FAISS index

**New Report Template:**
- For HTML: modify `html_template.html` or create a new template, update `save_html_report` in `tools.py`
- For Markdown: the template is inline in `save_report` tool function (`tools.py` line 228)

## Special Directories

**`faiss_db/`:**
- Purpose: FAISS vector index files for the knowledge base
- Generated: Yes (by `knowledge_base.py` or `rebuild_kb.py`)
- Committed: No (listed in `.gitignore`)

**`agent_fronted/public/images/`:**
- Purpose: Chart images generated at runtime by `fig_inter` tools
- Generated: Yes (by backend tool execution)
- Committed: No (generated content)

**`agent_fronted/public/reports/`:**
- Purpose: Diagnostic report files (HTML, Markdown) generated at runtime
- Generated: Yes (by `save_report` and `save_html_report` tools)
- Committed: No (generated content)

**`agent_fronted/.npm/`:**
- Purpose: npm cache (should be in `.gitignore`)
- Generated: Yes
- Committed: Should not be

## Module Dependencies

**Backend dependency graph:**
```
app.py
├── tools.py (imports: tools, sanitize_for_json, safe_json_dumps, parse_todos_from_tool_output)
│   ├── knowledge_base.py (imports: db_retriever via query_knowledge_base tool)
│   │   └── pdfs/ (PDF files for ingestion)
│   └── subagent/fault_explanation_agent.py (imports: create_fault_explanation_agent)
│       ├── subagent/call_api_tool.py (imports: tools list with query_fault_data_and_call_api, fig_inter)
│       └── subagent/fault_explanation_system_prompt.py (imports: FAULT_EXPLANATION_SYSTEM_PROMPT)
├── prompt_template.py (imports: systemprompt, get_identity_system_prompt)
└── html_template.html (read at runtime by save_html_report tool)
```

**Frontend dependency graph:**
```
main.ts
└── App.vue
    ├── stores/userIdentity.ts
    └── views/CustomerService.vue
        ├── components/ChatMessage.vue
        ├── components/ChatSidebar.vue
        ├── components/TaskPanel.vue
        ├── composables/useChatStream.ts
        │   └── services/api.js
        ├── composables/useTodosPanel.ts
        │   └── services/api.js
        ├── hooks/useVoice.js
        ├── config/questionTemplates.ts
        ├── stores/userIdentity.ts
        └── utils/identityUtils.ts
            └── stores/userIdentity.ts
```

## Configuration Hierarchy

**Backend configuration loading:**
1. `.env` file loaded via `load_dotenv(override=True)` at module level in `app.py`, `tools.py`, `subagent/call_api_tool.py`
2. Environment variables read via `os.getenv()` at usage sites
3. No layered config (no `.env.local`, `.env.production` distinction)
4. Some values are hardcoded: Ollama embedding URL (`http://10.108.13.254:11434` in `knowledge_base.py`), ML API URL (`http://10.108.13.250:8001/predict_reason` in `subagent/call_api_tool.py`), DCMA database name (`dcma` hardcoded in `tools.py` line 35)

**Frontend configuration loading:**
1. `vite.config.ts`: Build-time config (port 9005, proxy rules, path aliases)
2. `agent_fronted/src/services/api.js`: `BASE_URL` hardcoded to `http://localhost:8000` (line 1)
3. `VITE_BACKEND_BASE` env variable optionally used in `ChatMessage.vue` for image URL resolution
4. No `.env` file for frontend detected

---

*Structure analysis: 2026-03-26*
