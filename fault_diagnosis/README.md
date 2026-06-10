# fault_diagnosis 后端说明

本文档描述 `fault_diagnosis/` 后端源码的架构、模块职责、Agent 工作流，以及当前可靠性分析和证据治理机制。

## 后端定位

`fault_diagnosis/` 是项目唯一的后端源码根。它承担四类职责：

1. 对前端和语音网关提供 FastAPI HTTP/SSE 接口。
2. 初始化并运行 LangChain/LangGraph Agent、Workflow V1 场景流和 legacy ReAct 兼容流。
3. 连接 MySQL、PostgreSQL、FAISS/Ollama、本地报告目录、管理员上传 PDF 知识库等外部资源。
4. 对诊断结论做证据登记、证据覆盖评分、报告门禁、治理快照和台账留痕。

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
        +-- agent_runtime/       SSE 适配、流控制、Workflow/legacy 执行引擎
        +-- workflows/           Workflow V1 场景、步骤、产物和 planner
        +-- runtime/             工具运行时、证据桥接、阶段追踪、开发模式
        +-- tools/               LangChain 工具：SQL、知识库、报告、搜索、工单、时间
        |
        +-- quality/             evidence registry、quality gate、safe action guard
        +-- knowledge/           基础 PDF FAISS 知识库和上传 PDF 知识库
        +-- repositories/        文件型仓储和历史索引
        +-- mcp/                 MCP tool/resource 封装
```

真实模式启动时，`app_lifespan` 会依次做这些初始化：

1. 校验 `SESSION_SECRET`、`FRONTEND_ORIGINS` 等部署安全配置。
2. 初始化 MySQL 异步连接池。
3. 预加载本地 FAISS 知识库索引，索引缺失时只告警，不在线重建全量知识库。
4. 创建 PostgreSQL `AsyncPostgresSaver`，用于 LangGraph 对话状态持久化。
5. 创建 `chat_model`、`summary_model`、middleware 和 LangChain tools。
6. 通过 `create_agent` 创建 legacy LangGraph ReAct agent，并挂到 `app.state.agent`。

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
| `app_lifespan.py` | 应用生命周期初始化和资源清理，负责真实模式下的 DB、知识库、checkpointer、agent 初始化。 |
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
| `report_tools.py` | `save_report`、`save_html_report`，生成 Markdown/HTML 报告，并执行报告可信度门禁和安全降级。 |
| `work_order_tools.py` | 工单草稿或下发类工具，受 safe action guard 约束。 |
| `utility_tools.py` | 当前时间和搜索工具。 |
| `__init__.py` | 汇总运行时工具，真实 agent 会追加 SQL toolkit 工具。 |

### knowledge

| 模块 | 职责 |
| --- | --- |
| `base.py` | 基础 PDF 知识库构建、加载、状态检查、FAISS/Ollama embeddings、SQLite embedding 缓存。 |
| `uploaded_pdf_kb.py` | 管理员上传 PDF 的轻量知识库、语料检索和可选向量索引。 |

### agent_runtime

| 模块 | 职责 |
| --- | --- |
| `streaming.py` | `/chat/stream` 的统一 SSE 入口，根据配置和场景选择 Workflow V1、报告续写、dev stream 或 legacy ReAct。 |
| `workflow_engine.py` | 将 Workflow runner 输出的 SSE chunk 适配成统一 trace/thread 外壳。 |
| `legacy_react_engine.py` | LangGraph ReAct 兼容链路，消费 agent `astream_events`，过滤内部工具文本，输出 token/tool/complete 事件。 |
| `sse_adapter.py` | 统一 SSE 编码、trace 注入、complete payload 增强和错误 payload。 |
| `stream_control.py` | stream_id 注册、取消和清理，支持 `/chat/stop`。 |
| `event_contracts.py` | SSE 结构化事件模型。 |
| `error_classification.py` | 模型网关、知识库、工具、内部错误分类。 |
| `middleware.py` | legacy ReAct agent 的 middleware 组装。 |

### workflows

| 模块 | 职责 |
| --- | --- |
| `router.py` | 根据关键词、故障码、设备、时间范围、证据复核意图等选择 workflow。 |
| `runner.py` | 构建对应场景 runner，统一输出 Workflow V1 SSE。 |
| `contracts.py` | Pydantic 合同：请求、路由、各步骤 artifact、planner artifact、线程级 artifact envelope。 |
| `boundary_specs.py` | 各 workflow 需要的能力边界，如 SQL、知识库、报告、上游 artifact。 |
| `prompts.py` | 请求理解、SQL 生成、分析、最终回答、复核等 prompt 构造。 |
| `agents/planner.py` | planner 子 Agent，生成执行目标、证据需求、约束、风险和成功标准；失败时规则 fallback。 |
| `steps/` | 请求解析、SQL 执行、知识检索、报告构建、证据复核等可复用步骤。 |
| `scenarios/` | 具体场景 runner：故障诊断、状态巡检、手册问答、报告生成、澄清、证据复核。 |
| `artifact_store.py` | 线程级结构化产物存储 facade，默认文件后端，也支持 memory/postgres。 |
| `artifact_backends/` | artifact store 的 file、memory、postgres 实现。 |
| `adapters.py` | Workflow 对现有 tools 和 legacy 输出的薄适配层。 |
| `report_mapper.py` | 将上游 artifact 映射为报告输入。 |

### quality

| 模块 | 职责 |
| --- | --- |
| `evidence.py` | 请求级 evidence registry、工具证据登记、finding 抽取、证据匹配、覆盖评分、quality gate。 |
| `governance.py` | 统一治理快照、报告门禁、工单门禁、Workflow V1 evidence bundle。 |
| `safe_actions.py` | 高风险动作安全守卫，证据不足时发布动作自动降级为草稿或阻止。 |

### runtime

| 模块 | 职责 |
| --- | --- |
| `session_store.py` | 基于 contextvars 的请求级 namespace，存储 evidence registry、工具产物元数据等上下文。 |
| `diagnosis_runtime.py` | legacy 最终答案治理封装，生成 grounded final content、evidence quality 和 quality gate notice。 |
| `execution_runtime.py` | legacy 流式执行中的工具生命周期、阶段追踪和 evidence/finding 关联。 |
| `tool_runtime.py` | 工具结束时登记证据，并构造标准 `tool_start`/`tool_end` payload。 |
| `workflow_runtime.py` | 工具名到 workflow stage 的映射，以及阶段开始/完成统计。 |
| `workflow_contract_adapter.py` | 工作流合同兼容适配。 |
| `dev_mode.py` | 本地开发模式的模拟消息、Todo 和 SSE 输出。 |

### repositories

| 模块 | 职责 |
| --- | --- |
| `history_index.py` | session 到 thread 的历史索引。 |
| `admin_pdf_repository.py` | 管理员 PDF 文件记录、状态和元数据。 |
| `admin_pdf_registry_storage.py` | PDF registry 文件存储辅助。 |
| `governance_repository.py` | 治理快照、治理台账的文件存储。 |

### mcp

`mcp/` 提供面向外部工具协议的封装。`server.py` 维护 tool/resource 注册、请求校验、trace/run id、错误协议化；`tools/handlers.py` 中的 MCP 工具复用 Workflow runner、MySQL 查询、知识库检索、报告生成和证据质量评估能力。

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

`agent_runtime/streaming.py` 中的 `token_stream_events` 是分流核心。

当前优先级：

1. 如果用户是在上一轮诊断或巡检结果基础上要求“生成报告”，并且当前 thread 有上游 artifact，则直接走 Workflow V1 报告生成流。
2. 如果 `ENABLE_WORKFLOW_V1=true`，并且不是编辑重生成、不是本地 dev mode，则进入 Workflow V1 主链路。
3. 如果是 dev mode，则进入模拟流。
4. 其他情况进入 legacy LangGraph ReAct 兼容链路。

所有链路最后都会通过 SSE adapter 注入 `trace_id`、`thread_id`，并输出兼容前端的事件：

```text
start -> ping* -> tool_start/tool_end* -> token* -> complete
```

异常会转成 `server_error`，并按模型、知识库、工具、内部错误等类别给出可重试标记。

### 3. Workflow V1 路由

Workflow V1 使用 `workflows/router.py` 做规则路由。它会识别这些场景：

| workflow_type | 场景 |
| --- | --- |
| `fault_diagnosis` | 故障诊断、异常分析、告警/报警原因判断、可否出报告判断。 |
| `status_inspection` | 当前状态、健康概览、巡检、趋势、风险摘要。 |
| `manual_qa` | 手册问答、操作说明、安全注意事项、故障码含义。 |
| `report_generation` | 基于上一轮诊断/巡检 artifact 生成报告。 |
| `clarification` | 请求太泛、槽位缺失或候选 workflow 歧义较大，需要先追问。 |
| `evidence_review` | 用户明确问“证据链、依据、复核、可靠吗、哪里不够”。 |

路由结果是 `WorkflowRouteResult`，包含：

- `workflow_type`
- `confidence`
- `reason`
- `needs_sql`
- `needs_knowledge`
- `needs_report`
- `candidate_workflows`
- `missing_slots`
- `disambiguation_needed`
- `review_needed`
- `upstream_artifact_required`

`workflows/runner.py` 根据路由结果创建对应 `BaseScenarioRunner` 子类。

### 4. 故障诊断 Workflow V1 主链路

`FaultDiagnosisRunner` 是当前最核心的场景。同步执行和 SSE 执行的业务步骤一致：

```text
parse_request
  -> planning
  -> sql
  -> knowledge
  -> analysis
  -> report
  -> final_answer
  -> save_artifact
```

详细说明：

1. `parse_request`
   - 使用 `build_understanding_prompt` 让模型把用户问题转成 `DiagnosisRequest`。
   - 提取设备、指标、故障码、时间范围、报告需求和分析目标。
   - JSON 解析失败时会走 JSON repair prompt。

2. `planning`
   - `create_planning_artifact` 调用 planner 子 Agent。
   - 输出任务摘要、诊断目标、所需证据、执行约束、风险标记、成功标准。
   - planner 异常时使用 `build_default_plan` 规则计划兜底。

3. `sql`
   - 使用 `build_sql_generation_prompt` 生成 SQL。
   - 故障诊断场景限制可用表：`real_data`、`device_alarm`、`device_metric`、`device_fault_data`、`fault_records`。
   - 若生成了未知表，会回退到 `real_data` 最近数据查询。
   - 执行前优先经过 `sql_db_query_checker`，再调用 `sql_db_query`。
   - 输出 `SqlStepArtifact`，包含 SQL、摘要、结果预览、原始输出。

4. `knowledge`
   - 基于设备、故障码和分析目标构造知识库查询。
   - 调用 `query_knowledge_base`。
   - 同时检索基础 PDF FAISS 知识库和管理员上传 PDF 知识库。
   - 输出 `KnowledgeStepArtifact`。

5. `analysis`
   - 将结构化请求、SQL artifact、知识 artifact、当前时间、planner artifact 一起送入分析 prompt。
   - 输出 `AnalysisStepArtifact`，包括结论、依据、建议、风险提示、缺失信息和置信度。

6. `report`
   - 如果 `request.needs_report=true`，调用 `save_report` 生成 Markdown 报告。
   - 生成前会构造 Workflow evidence bundle，用于报告可信度门禁。
   - 报告保存工具会根据门禁决定正式发布或降级草稿。

7. `final_answer`
   - 使用最终回答 prompt 整理面向用户的文本。
   - 模型整理失败时回退模板输出。

8. `save_artifact`
   - 将本轮结构化结果保存为 `WorkflowArtifactEnvelope`。
   - artifact 中保存 request、SQL、知识、分析、报告、planner、route_result、governance、证据快照、finding 快照。
   - 后续“生成报告”“证据复核”会读取这个 artifact。

### 5. Legacy ReAct 兼容链路

legacy 链路在 `LegacyReactStreamEngine` 中执行，保留 LangGraph ReAct Agent 的动态工具调用能力。

核心过程：

1. 构造 `HumanMessage`，必要时用 `RemoveMessage` 覆盖历史。
2. 调用 `app.state.agent.astream_events`。
3. 过滤模型流中泄露的内部工具 JSON、SQL 草稿和非用户可见文本。
4. 将工具开始、结束事件转成 `tool_start`、`tool_end`、`tool_progress`、`tool_stream`。
5. 工具结束时调用 `register_tool_runtime_evidence` 登记 SQL、RAG、图表、报告或工单证据。
6. 模型无首事件或首阶段失败时，尝试非流式 fallback。
7. 生成完成后调用 `build_diagnosis_runtime_payload` 做 evidence quality、grounded content 和 quality gate。
8. 如 evidence gate 被 blocked 且缺少 SQL 或 RAG，会自动补查一次 `sql_db_query` 或 `query_knowledge_base`。
9. 最终输出 `complete`，其中包含 evidence、finding、quality gate、governance、阶段详情和 lifecycle ledger。
10. 同时把 legacy 输出桥接保存成 `WorkflowArtifactEnvelope`，供后续报告生成或证据复核使用。

## 可靠性分析和证据治理

当前可靠性分析不是单一模型打分，而是一套“证据登记 -> 结论抽取 -> 证据绑定 -> 覆盖评分 -> 门禁决策 -> 输出降级/留痕”的管线。

### 1. 证据从哪里来

证据主要由 `quality/evidence.py` 统一登记，保存在请求级 namespace 中。

证据类型：

| type | 来源 | 说明 |
| --- | --- | --- |
| `sql` | `sql_db_query`、`sql_db_schema`、`sql_db_list_tables`、`sql_inter` | 结构化数据证据，通常权重最高。 |
| `rag` | `query_knowledge_base` | 基础 PDF 或上传 PDF 的知识片段。 |
| `chart` | `fig_inter` | 图表证据，记录图表文件、数据源、行数、图表类型。 |
| `report` | `save_report`、`save_html_report` | 报告产物证据。 |
| `action` | `create_work_order` | 工单或动作产物证据。 |
| `generic` | artifact 回填等 | 无法归类时的保守证据。 |

每条 evidence record 包含：

- `evidence_id`
- `type`
- `source`
- `title`
- `summary`
- `raw_ref`
- `stage`
- `created_at`
- `metadata`

`normalize_evidence_record` 会把原始证据进一步标准化成：

- `kind`
- `family`: `telemetry`、`knowledge`、`artifact`、`action`、`generic`
- `channel`: `database`、`retrieval`、`visualization`、`reporting`、`execution`
- `source_locator`
- `source_details`
- `artifact`
- `governance`
- `payload`

### 2. 结论如何抽取

`create_grounded_findings(final_content)` 会把最终回答拆成最多 5 条 finding。

拆分规则：

- 按句号、分号、换行拆分。
- 去掉列表前缀和 Markdown 装饰。
- 过滤太短、过泛、无关键词、纯标题或系统提示类文本。
- 对每条 finding 识别严重度和置信度。

严重度基于关键词：

- high: 停机、中断、高风险、严重、过载、报警、告警、异常等。
- medium: 波动、偏高、偏低、退化、超限等。
- low: 其他可行动结论。

置信度基于证据匹配：

- evidence_count 为 0 时是 low。
- match_score 较高或 evidence_count 较多时升到 medium/high。

### 3. finding 如何绑定 evidence

`_match_evidence_for_finding` 会对每条 finding 做关键词匹配。

匹配材料包括：

- finding 文本中的领域词、故障码、设备号、英文/数字标识。
- evidence 的标题、摘要、source、SQL query、图表名、图表类型、报告路径等。

匹配排序会偏向：

- 匹配分数更高的证据。
- SQL 证据。
- RAG 证据。
- collect/retrieve 阶段的证据。

每条 finding 最多绑定 3 条普通 evidence，并额外记录最多 2 条 chart evidence。绑定结果写入 `finding_links`：

- `finding_id`
- `evidence_ids`
- `chart_evidence_ids`
- `matched_keywords`
- `match_score`

没有显式匹配时，会保守回填前两条证据作为弱关联，但 `match_score=0`，后续 quality gate 会把它视为未强支撑。

### 4. 覆盖评分怎么算

`summarize_evidence_coverage` 会生成覆盖评分和等级。

主要指标：

- `sql_count`
- `rag_count`
- `chart_count`
- `linked_chart_count`
- `orphan_chart_count`
- `total_findings`
- `linked_findings`
- `finding_binding_rate`
- `chart_coverage_rate`

评分公式：

```text
score =
  25 if sql_count > 0 else 0
+ 25 if rag_count > 0 else 0
+ finding_binding_rate * 0.3
+ chart_coverage_rate * 0.2
```

等级：

| score | grade |
| --- | --- |
| >= 85 | A |
| >= 70 | B |
| >= 50 | C |
| < 50 | D |

这意味着当前系统认为 SQL 和 RAG 是可靠性底座，finding 与证据的绑定率决定结论是否站得住，图表只作为可解释性和可视化补强。

### 5. quality gate 怎么决策

`summarize_evidence_quality` 输出总的质量门禁：

| gate | 含义 | 输出策略 |
| --- | --- | --- |
| `pass` | 证据足够，风险低 | 可以正常回答和正式报告。 |
| `review_required` | 有证据但仍需人工复核 | 回答要保守，报告通常只能作为初步报告。 |
| `blocked` | 证据不足或结论支撑失败 | 不应给确定性结论，也不建议生成正式报告。 |

会触发降级或阻断的条件：

- 没有抽取到 structured findings: `review_required`。
- 没有任何 evidence records: `blocked`。
- 存在未支撑 finding: 视数量进入 `review_required` 或 `blocked`。
- 存在 low confidence finding: 至少 `review_required`。
- finding 和 evidence 只有弱匹配: 至少 `review_required`。
- 覆盖等级是 C/D 时会加入 review reason；如果缺少 RAG 且匹配关键词过少，可能升级为 `blocked`。

输出字段包括：

- `gate`
- `risk_level`
- `total_findings`
- `total_evidences`
- `linked_findings`
- `unsupported_findings`
- `weak_match_findings`
- `low_confidence_findings`
- `coverage_ratio`
- `review_reasons`
- `recommended_action`
- `missing_evidence_ids`
- `coverage_summary`
- `release_ready`

### 6. 输出如何被门禁影响

legacy 链路会在 `build_diagnosis_runtime_payload` 中应用门禁：

1. `apply_grounding_status_to_findings`
   - 未支撑、低置信、blocked 中弱匹配的 finding 会被标记为 `pending`。
   - 展示文本前会加 `待确认：`。

2. `build_grounded_final_content`
   - 对已绑定证据的 finding 追加 `[证据: ev_xxx]`。

3. `build_quality_gated_response`
   - `pass`: 保持正常输出。
   - `review_required`: 在回答开头提示“需要保守解读”，并说明证据门禁原因和下一步建议。
   - `blocked`: 不再输出确定性诊断，改为“当前诊断结论仍待确认”，只保留待确认假设和补证建议。

4. `build_quality_gate_notice`
   - 生成给前端或报告使用的门禁说明。

### 7. Workflow V1 的可靠性快照

Workflow V1 不依赖 legacy 请求级 registry 里的全量工具事件，而是在 `build_workflow_evidence_bundle` 中基于本场景 artifact 构造快照：

1. SQL 成功时生成 `workflow-sql-1`。
2. 知识库有结果时生成 `workflow-rag-1`。
3. 对分析结论抽关键词。
4. 与 SQL/RAG 证据标题、摘要和 query 做关键词重叠评分。
5. 如果有 SQL 但关键词没明显匹配，会给 SQL 一个保守兜底关联分 `35`，但这通常不足以通过正式门禁。

Workflow V1 的关键阈值：

| 条件 | 门禁结果 |
| --- | --- |
| 没有匹配证据 | `blocked`，高风险。 |
| 最佳匹配分 `< 45` | `blocked`，认为 finding 和证据关联太弱。 |
| 最佳匹配分 `45-69` | `review_required`，可初步输出但不建议正式下结论。 |
| 最佳匹配分 `>= 70` | 保留原有门禁结果，若 SQL、知识和高置信都满足则可 `pass`。 |

此外：

- 缺 SQL: 直接 `blocked`，高风险。
- 知识库是必需但缺失: `review_required`。
- 分析置信度不是 high: `review_required`。

故障诊断 runner 会把这些结果存入：

- `payload.governance`
- `payload.report_gate_summary`
- `payload.findings_snapshot`
- `payload.finding_links_snapshot`
- `payload.evidence_records_snapshot`

这些字段同时用于 SSE `complete`、报告生成和后续证据复核。

### 8. 报告和工单如何安全降级

`quality/safe_actions.py` 提供 `build_safe_action_guard`。

当报告或工单这类高风险动作执行时，会检查：

- `gate`
- `risk_level`
- `release_ready`
- `review_reasons`

结果：

| 条件 | action/status | 文件或动作 |
| --- | --- | --- |
| `release_ready=true` 且 `gate=pass` | `publish/published` | 允许正式输出。 |
| 证据不足但允许草稿 | `draft/draft` | 文件名加 `pending-review` 后缀。 |
| 证据不足且不允许草稿 | `block/blocked` | 文件名加 `blocked` 后缀或阻止动作。 |

因此即使模型尝试直接生成报告，后端也会把证据不足的正式输出降级成草稿。

### 9. 证据复核 workflow

当用户问“这个结论可靠吗、证据链够吗、哪里还不够、复核一下”等，路由会进入 `EvidenceReviewRunner`。

流程：

```text
build_request
  -> planning
  -> load_upstream_artifact
  -> build_evidence_review_artifact
  -> final_answer
```

关键点：

1. 只复核当前 thread 最近一次 `fault_diagnosis`、`status_inspection` 或 `manual_qa` artifact。
2. 如果当前请求级 evidence registry 是空的，会从上游 artifact 的 `evidence` 保守回填。
3. 如果当前 registry 没有 findings，会从上游 `final_answer` 重新抽取 findings。
4. 再次调用 `summarize_evidence_coverage` 和 `summarize_evidence_quality`。
5. 输出 `EvidenceReviewArtifact`：
   - `review_target_workflow`
   - `total_findings`
   - `total_evidences`
   - `coverage_score`
   - `quality_gate_status`
   - `unsupported_findings`
   - `missing_evidence_ids`
   - `recommended_action`
   - `review_summary`

用户看到的是面向业务的复核结论，而不是原始打分字段。

### 10. 自动补证逻辑

legacy 链路在最终质量评估后有一次自动补证机会：

```text
如果 evidence_quality.gate == blocked:
  如果 sql_count <= 0: 自动补查 sql_db_query
  否则如果 rag_count <= 0: 自动补查 query_knowledge_base
```

自动补证后会重新登记 evidence，并重新生成 diagnosis payload。这个机制只补一步，避免在同一轮里反复调用工具。

## 运行时持久化

| 数据 | 默认位置或后端 |
| --- | --- |
| LangGraph 对话状态 | PostgreSQL `AsyncPostgresSaver` |
| session/thread 归属 | 签名 cookie 加本地历史索引 |
| Workflow artifact | 默认 `trash/run/workflow_artifacts/*.jsonl`，可通过 `WORKFLOW_ARTIFACT_BACKEND` 切换 |
| 基础知识库 | `faiss_db/` |
| 上传 PDF 文件和状态 | `trash/run/admin_uploads` 及对应 registry |
| 报告 | `agent_fronted/public/reports` 或配置的 reports 目录 |
| 治理快照和台账 | reports 目录下的治理文件 |
| JSON 日志 | `trash/run/app-json.log` |

## 常见扩展点

1. 新增 HTTP 能力：在 `api/` 新增 router，在 `services/` 放业务编排，在 `api/app_routes.py` 注册。
2. 新增 Agent 工具：在 `tools/` 实现 LangChain tool，并在 `tools/__init__.py` 的 `get_runtime_tools` 中加入。
3. 新增 Workflow 场景：补充 `WorkflowType`、`boundary_specs`、`router` 规则、`scenarios/` runner，并在 `runner.py` 映射。
4. 调整可靠性策略：优先改 `quality/evidence.py` 的覆盖评分和 gate 条件，或改 `quality/governance.py` 的 Workflow V1 evidence bundle 阈值。
5. 更换 artifact 存储：设置 `WORKFLOW_ARTIFACT_BACKEND=file|memory|postgres`，必要时配置 `WORKFLOW_ARTIFACT_TABLE` 或 `WORKFLOW_ARTIFACT_POSTGRES_DSN`。
