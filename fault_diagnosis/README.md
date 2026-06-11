# fault_diagnosis 后端说明

本文档描述 `fault_diagnosis/` 后端源码的架构、模块职责，以及当前最小 Agent 运行链路。

## 后端定位

`fault_diagnosis/` 是项目唯一的后端源码根。它承担四类职责：

1. 对前端和语音网关提供 FastAPI HTTP/SSE 接口。
2. 初始化并运行限制型单 Agent 主链路。
3. 连接 MySQL、PostgreSQL、FAISS/Ollama、本地报告目录、管理员上传 PDF 知识库等外部资源。
4. 保存对话状态、线程级结构化产物、报告文件和运行日志。

启动入口：

```bash
python -m fault_diagnosis.app
```

生产部署入口：

```bash
gunicorn -w 4 -k uvicorn.workers.UvicornWorker fault_diagnosis.app:app --bind 0.0.0.0:8000
```

## 总体架构

```text
Frontend / Voice Gateway
        |
        | REST / SSE
        v
FastAPI app
  app.py -> app_factory.py
        |
        +-- api/                 HTTP 路由层
        +-- services/            应用服务层
        +-- auth/                session、管理员身份和 thread 归属
        +-- infrastructure/      CORS、静态目录、模型、lifespan、数据库池
        |
        +-- agent_runtime/       SSE 适配、流控制和主链路调度
        +-- single_agent/        限制型单 Agent、trace schema、受限工具编排
        +-- workflows/           复用步骤、产物合同和 artifact store
        +-- runtime/             请求级命名空间、开发模式和合同适配
        +-- tools/               LangChain 工具：SQL、知识库、报告等
        |
        +-- knowledge/           基础 PDF FAISS 知识库和上传 PDF 知识库
        +-- repositories/        文件型仓储和历史索引
```

真实模式启动时，`app_lifespan` 会依次做这些初始化：

1. 校验 `SESSION_SECRET`、`FRONTEND_ORIGINS` 等部署安全配置。
2. 初始化 MySQL 异步连接池。
3. 预加载本地 FAISS 知识库索引，索引缺失时只告警，不在线重建全量知识库。
4. 设置 `app.state.checkpointer`、`app.state.agent`、`app.state.pool` 为 `None`；主链路不再初始化旧 LangGraph checkpointer/agent。

`LOCAL_DEV_MODE=true` 时会跳过外部依赖初始化，改用 `runtime/dev_mode.py` 中的本地模拟状态。

## 模块职责

### 入口与应用组装

| 模块 | 职责 |
| --- | --- |
| `app.py` | 后端进程入口，执行 runtime bootstrap，创建 FastAPI app，并在直接运行时启动 uvicorn。 |
| `app_factory.py` | 构建 FastAPI app，挂载 session manager、history repository、模型、CORS、静态资源和路由。 |
| `config.py` | 集中读取环境变量，管理知识库、Agent、数据库、会话、管理员、OCR、TTS 等配置。 |

### infrastructure

| 模块 | 职责 |
| --- | --- |
| `app_bootstrap.py` | 进程启动前的编码、日志和运行目录准备。 |
| `app_lifespan.py` | 应用生命周期初始化和资源清理，负责真实模式下的 MySQL、知识库预加载和运行状态初始化。 |
| `app_models.py` | 创建 OpenAI 兼容 `ChatOpenAI` 模型实例。 |
| `app_setup.py` | 创建 `SessionScopeManager`，配置 CORS。 |
| `app_static.py` | 挂载前端构建产物、`/reports`、`/images` 等静态资源。 |
| `db_pool.py` | MySQL 异步连接池初始化、获取和关闭。 |
| `server_runner.py` | 封装 uvicorn 启动参数。 |

### api

| 模块 | 主要接口 |
| --- | --- |
| `chat.py` | `/chat/stream`、`/chat/stream/edit`、`/chat/stop`、`/agent/chat`。 |
| `history.py` | `/ai/history/{type}`、`/ai/history/{type}/{chat_id}`、`/api/todos/{thread_id}`。 |
| `auth.py` | `/auth/identity`、`/auth/admin/login`、`/auth/logout`。 |
| `admin_pdfs.py` | 管理员 PDF 上传、详情、文件读取、知识库归档、人工校正和删除。 |
| `governance.py` | 治理快照保存、查询，治理台账创建、查询、更新。 |
| `health.py` | 依赖健康检查、OCR 健康检查。 |
| `tts.py` | TTS 合成代理接口。 |
| `meta.py` | 根路径和基础元信息。 |
| `app_routes.py` | 统一注册所有 router。 |

### services

| 模块 | 职责 |
| --- | --- |
| `chat_service.py` | 聊天流、编辑重生成、语音 JSON 兼容接口、停止生成的用例编排。 |
| `history_service.py` | 当前 session 归属下的历史列表、消息详情、删除和 Todo 查询。 |
| `admin_pdf_service.py` | PDF 上传、状态查询、归档、校正和删除的应用服务。 |
| `admin_pdf_pipeline.py` | PDF 文本抽取、OCR、上传知识库入库等后台流程。 |
| `governance_service.py` | 治理快照和治理台账的应用服务。 |
| `health_service.py` | MySQL、PostgreSQL、Ollama、LLM、Tavily、知识库等依赖检查。 |
| `tts_service.py` | TTS 请求转发、超时和错误处理。 |

### auth

| 模块 | 职责 |
| --- | --- |
| `session_scope.py` | 服务端 session cookie、legacy thread 映射、thread 归属校验和 cookie 回写。 |
| `admin_auth.py` | 管理员身份 cookie、登录校验、身份解析。 |

### tools

| 模块 | 职责 |
| --- | --- |
| `sql_tools.py` | 基于 MySQL `SQLDatabaseToolkit` 提供 `sql_db_query`、`sql_db_schema`、`sql_db_query_checker` 等 SQL 工具。 |
| `kb_tools.py` | `query_knowledge_base`，查询基础 PDF FAISS 知识库和管理员上传 PDF 知识库，并登记 RAG 证据。 |
| `report_tools.py` | `save_report`、`save_html_report`，生成 Markdown/HTML 报告。 |
| `work_order_tools.py` | 创建本地工单 JSON 产物。 |
| `utility_tools.py` | 当前时间和搜索工具。 |
| `__init__.py` | 工具包导出辅助；单 Agent 主链路直接按白名单调用所需工具。 |

### knowledge

| 模块 | 职责 |
| --- | --- |
| `base.py` | 基础 PDF 知识库构建、加载、状态检查、FAISS/Ollama embeddings、SQLite embedding 缓存。 |
| `uploaded_pdf_kb.py` | 管理员上传 PDF 的轻量知识库、语料检索和可选向量索引。 |

### agent_runtime

| 模块 | 职责 |
| --- | --- |
| `streaming.py` | `/chat/stream` 的统一 SSE 入口；dev mode 走模拟流，真实模式调度限制型单 Agent。 |
| `sse_adapter.py` | 统一 SSE 编码、trace 注入、complete payload 增强和错误 payload。 |
| `stream_control.py` | stream_id 注册、取消和清理，支持 `/chat/stop`。 |
| `event_contracts.py` | SSE 结构化事件模型。 |
| `error_classification.py` | 模型网关、知识库、工具、内部错误分类。 |

### single_agent

| 模块 | 职责 |
| --- | --- |
| `contracts.py` | `AgentTrace`、`TraceEvent`、`SingleAgentDecision`、`SingleAgentLimits` 等 trace 与限制合同。 |
| `prompts.py` | 限制型单 Agent 的请求理解与分析 prompt。 |
| `runner.py` | 固定主流程编排：请求理解、受限 SQL、知识库检索、分析、可选报告、最终回答，并输出兼容 SSE。 |

### workflows

| 模块 | 职责 |
| --- | --- |
| `contracts.py` | Pydantic 合同：请求、各步骤 artifact、线程级 artifact envelope。 |
| `steps/` | 请求解析、SQL 执行、知识检索、报告构建等可复用步骤。 |
| `artifact_store.py` | 线程级结构化产物存储 facade，默认文件后端，也支持 memory/postgres。 |
| `artifact_backends/` | artifact store 的 file、memory、postgres 实现。 |
| `adapters.py` | 单 Agent 使用的工具薄适配层。 |
| `report_mapper.py` | 将上游 artifact 映射为报告输入。 |

### runtime

| 模块 | 职责 |
| --- | --- |
| `session_store.py` | 基于 contextvars 的请求级 namespace。 |
| `workflow_contract_adapter.py` | 线程级诊断产物到前端合同的兼容适配。 |
| `dev_mode.py` | 本地开发模式的模拟消息、Todo 和 SSE 输出。 |

### repositories

| 模块 | 职责 |
| --- | --- |
| `history_index.py` | session 到 thread 的历史索引。 |
| `admin_pdf_repository.py` | 管理员 PDF 文件记录、状态和元数据。 |
| `admin_pdf_registry_storage.py` | PDF registry 文件存储辅助。 |
| `governance_repository.py` | 治理快照、治理台账的文件存储。 |

## 当前 Agent 流程

### 1. 请求进入后端

主入口是 `GET /chat/stream`。

调用链：

```text
api/chat.py
  -> ChatService.stream_chat
  -> token_stream_events
```

`ChatService.stream_chat` 会做这些事：

1. 解析当前 session 和管理员身份，忽略前端传来的不可信 `user_identity`。
2. 校验或签发 `thread_id`，只允许访问当前 session 拥有的 thread。
3. 登记历史索引。
4. 注册 `stream_id`，用于后续 `/chat/stop` 取消生成。
5. 返回 `StreamingResponse`，由 `token_stream_events` 持续产出 SSE。

`POST /agent/chat` 是语音网关兼容接口。它不复制另一套 agent 逻辑，而是内部消费同一条 SSE 流，把 `token`、`complete` 和部分 `tool_end` 聚合为 JSON。

### 2. 统一流式调度

`agent_runtime/streaming.py` 中的 `token_stream_events` 是流式调度入口。

当前优先级：

1. 如果是 `LOCAL_DEV_MODE=true`，进入模拟流。
2. 其他请求进入 `RestrictedSingleAgentRunner`。

单 Agent 输出前端已支持的 SSE 事件：

```text
start -> ping* -> tool_start/tool_end* -> token* -> complete
```

异常会转成 `server_error`，并按模型、知识库、工具、内部错误等类别给出可重试标记。

### 3. 限制型单 Agent 主链路

`RestrictedSingleAgentRunner` 是当前主链路。它不做多 agent 协作，不做规划子流程，不做可靠性评分或报告门禁。

固定流程：

```text
understand
  -> sql（按 decision 可跳过）
  -> knowledge（按 decision 可跳过）
  -> analysis
  -> report（按 decision 可跳过）
  -> final_answer
  -> save_artifact
```

受限工具白名单：

- `sql_db_query_checker`
- `sql_db_query`
- `query_knowledge_base`
- `save_report`

默认限制：

- `max_rounds=6`
- `max_tool_calls=4`

SQL 阶段只允许访问 `real_data`、`device_alarm`、`device_metric`、`device_fault_data`、`fault_records`，并且只允许只读 `SELECT/WITH` 查询；未知表或非只读 SQL 会回退到受限的最近数据查询。

如果用户要求“基于刚才/上一轮结果生成报告”，并且当前 thread 有结构化 artifact，单 Agent 会在同一 runner 内读取 artifact 并调用 `save_report`，不再分流到旧 report workflow。

### 4. Trace 合同

每次单 Agent 运行都会生成 `AgentTrace`，并在 `complete` payload 与保存的 `WorkflowArtifactEnvelope.payload.trace` 中携带。

trace 事件类型：

- `stage`：阶段开始、完成、跳过或失败。
- `decision`：请求理解后的能力决策，如是否需要 SQL、知识库、报告。
- `tool_call`：受限工具调用开始，包含工具名、run_id、输入摘要。
- `tool_result`：受限工具调用结果，包含结果预览和耗时。
- `artifact`：结构化产物，如 request、SQL artifact、knowledge artifact、analysis artifact、report artifact。
- `final_answer`：最终回答或错误终态。

当前 trace 不计算可靠性分数；后续可靠性分析应离线消费 trace 做后处理。

### 5. 共享产物层

`workflows/` 不再包含多流程调度器，只保留单 Agent 仍复用的结构：

1. `WorkflowArtifactEnvelope`、步骤 artifact 和报告 artifact 等历史命名的产物合同。
2. artifact store 及 file/memory/postgres 后端。
3. 请求解析、SQL 执行、知识检索、报告构建等可复用 step。
4. 报告续写所需的 artifact-to-report mapper。

## 可靠性评估状态

当前阶段已经移除旧的复杂可靠性治理代码，后端只保留最小可运行链路：

- 不再维护请求级 evidence registry。
- 不再抽取 finding、绑定 evidence 或计算 coverage score。
- 不再输出 `evidence_quality`、`report_gate`、`quality_gate_notice`、`release_ready` 等门禁字段。
- 报告和工单按普通产物保存，不再因为证据门禁自动降级为草稿或阻止输出。
- `evidence_review` workflow 已移除，后续可靠性评估将基于重构后的 trace 重新实现。

可靠性评估应消费 `AgentTrace` 做后处理，而不是插入主运行链路。

## 运行时持久化

| 数据 | 默认位置或后端 |
| --- | --- |
| session/thread 归属 | 签名 cookie 加本地历史索引 |
| Workflow artifact | 默认 `trash/run/workflow_artifacts/*.jsonl`，可通过 `WORKFLOW_ARTIFACT_BACKEND` 切换 |
| 基础知识库 | `faiss_db/` |
| 上传 PDF 文件和状态 | `trash/run/admin_uploads` 及对应 registry |
| 报告 | `agent_fronted/public/reports` 或配置的 reports 目录 |
| 治理快照和台账 | reports 目录下的历史文件；当前最小链路不再主动生成可靠性治理快照 |
| JSON 日志 | `trash/run/app-json.log` |

## 常见扩展点

1. 新增 HTTP 能力：在 `api/` 新增 router，在 `services/` 放业务编排，在 `api/app_routes.py` 注册。
2. 新增单 Agent 工具：在工具模块实现 LangChain tool，再在 `single_agent/runner.py` 的白名单和对应阶段中显式接入。
3. 调整诊断流程：优先修改 `single_agent/runner.py`；只有产物合同或可复用 step 需要变化时才改 `workflows/`。
4. 重建可靠性评估：基于 `AgentTrace` 新增独立后处理模块。
5. 更换 artifact 存储：设置 `WORKFLOW_ARTIFACT_BACKEND=file|memory|postgres`，必要时配置 `WORKFLOW_ARTIFACT_TABLE` 或 `WORKFLOW_ARTIFACT_POSTGRES_DSN`。
