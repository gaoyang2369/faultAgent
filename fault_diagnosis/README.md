# fault_diagnosis 后端说明

`fault_diagnosis/` 是 faultAgent 工业故障诊断系统的后端源码根。当前后端默认主链路是 Agent Engine V2：请求理解、上下文解析、skill 路由、ExecutionPlan 校验、受限 SQL、知识库检索、诊断分析、可选报告/工单建议、最终回答、诊断 artifact 保存。

它不是多 Agent 编排，也不是让 LLM 自由循环选择工具。后端只维护 Agent Engine V2 主链路；`AGENT_ENGINE_VERSION` 只兼容解析为 `v2`。代码里的 `workflow_*` 字段是前端和历史 artifact 兼容投影，不代表还有独立 workflow 系统。

## 项目定位

后端承担这些职责：

- 提供 HTTP / SSE 接口，管理服务端 session、cookie、thread ownership 和历史记录。
- 以服务端身份为准进行 RBAC + ABAC 授权，前端传入的 `user_identity` 不参与授权。
- 调用 Agent Engine V2 完成诊断流水线，唯一生产链路是 `CurrentUtteranceParse -> CanonicalTurnRequest -> per-goal decisions -> Canonical Goal DAG -> typed nodes -> GoalExecutionResult -> DeliverableResult -> composite output`。旧 Frame 只在 compatibility/debug 边界单向投影。
- 读 MySQL 运行数据、查本地/上传知识文件 知识库、生成私有 HTML 报告，并保存线程级 diagnosis artifact。
- 对工单和设备动作保持高风险边界：只能给建议、草稿和人工确认要求，不能自动派发工单，不能自动重启、停机、复位或修改参数。

旧任务类型、旧候选任务和旧意图列表只允许作为 SSE、artifact 和前端兼容投影存在，不再是内部 skill routing、plan、readiness 或节点启停输入。退役的 shadow/diff/gate 计划字段已退出生产主链路，不应作为当前架构核心理解。

## 启动方式

开发启动：

```bash
python -m fault_diagnosis.app
```

等价 uvicorn：

```bash
python -m uvicorn fault_diagnosis.app:app --host 0.0.0.0 --port 8000 --reload
```

生产启动示例：

```bash
gunicorn -w 4 -k uvicorn.workers.UvicornWorker fault_diagnosis.app:app --bind 0.0.0.0:8000
```

测试环境使用独立依赖集，`pytest-asyncio` 用于落实 `pytest.ini` 中的
`asyncio_mode=auto`，不要删除该配置来规避异步测试：

```bash
python -m pip install -r requirements-dev.txt
PYTHONPATH=. pytest -q
```

常用环境变量以当前代码为准：

- 运行模式：`APP_ENV` / `ENV`、`LOCAL_DEV_MODE`、`ENABLE_PLAN_ENDPOINT`、`ENABLE_DEV_AUTH`、`FRONTEND_ORIGINS`、`AGENT_ENGINE_VERSION=v2`。`AGENT_ENGINE_VERSION` 保留为兼容环境变量，非 `v2` 值会解析为 `v2`。
- MySQL：`HOST`、`PORT`、`MYSQL_PW`、`MYSQL_USER`、`DCMA_DB_NAME` / `DB_NAME`。
- OpenAI-compatible LLM：优先读取本地部署别名 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`，并兼容 `OPENAI_BASE_URL`、`OPENAI_API_KEY`、`MODEL_NAME`；另有 `AVAILABLE_MODEL_NAMES`、`SINGLE_AGENT_MODEL_TIMEOUT_SECONDS`、`SINGLE_AGENT_MODEL_INPUT_LIMIT_CHARS`。私网地址默认绕过系统代理，也可用 `LLM_BYPASS_PROXY=true` 强制开启；vLLM/Qwen 可用 `LLM_ENABLE_THINKING=false` 关闭 thinking。`AVAILABLE_MODEL_NAMES` 可用英文逗号配置前端可选白名单，API Key 只保留在服务端环境变量中。
- Grounded answer（开发/测试默认全量尝试，生产默认关闭）：`ENABLE_GROUNDED_ANSWER_SYNTHESIS`、`GROUNDED_ANSWER_ROLLOUT_PERCENT`、`ANSWER_MODEL_NAME`、`AVAILABLE_ANSWER_MODEL_NAMES`、`ANSWER_MODEL_REQUEST_TIMEOUT_SECONDS=15`、`ANSWER_MODEL_MAX_TOKENS=512`、`ANSWER_MODEL_CONCURRENCY=8`、`ANSWER_MODEL_INCLUDE_DETERMINISTIC_FALLBACK=true`、`ANSWER_SYNTHESIS_MAX_INPUT_CHARS=12000`、`ANSWER_SYNTHESIS_MAX_OUTPUT_CHARS=4000`、`ANSWER_SYNTHESIS_TEMPERATURE=0.0`。开发/测试未显式配置时启用并按 100% 调用；生产需显式设置 `ENABLE_GROUNDED_ANSWER_SYNTHESIS=true` 与所需灰度比例。Answer 模型不跟随单次聊天请求模型；每轮最多调用一次，并且只消费 V2 已授权事实。超时、Schema 或确定性校验失败时立即使用 `CompositePresenter` 原回答，不重试。最终 complete payload 的 `final_answer_source` 与 trace 的 `answer_synthesis.final_answer_source` 明确标记最终答案来自模型还是模板。旧 `ANSWER_SYNTHESIS_TIMEOUT_SECONDS` / `ANSWER_SYNTHESIS_MAX_OUTPUT_TOKENS` / `ANSWER_SYNTHESIS_MAX_CONCURRENCY` 仅保留为 deprecated 读取投影。
- 统一语义模型（开发/测试默认 `primary`，生产默认 `off`）：`LLM_SEMANTIC_MODE=off|shadow|primary`、`LLM_SEMANTIC_CALL_POLICY=always|auto|off`、`ENABLE_LLM_CONTEXT_SEMANTICS`、`LLM_SEMANTIC_CONCURRENCY=8`、`LLM_SEMANTIC_FAILURE_POLICY=deterministic_fallback`、`INTENT_MODEL_NAME`、`INTENT_MODEL_TIMEOUT_SECONDS=18`、`INTENT_MODEL_MAX_TOKENS=512`。当前验证阶段使用 `always`；生产稳定后推荐 `auto`，仅在未识别、多分句/多 Goal、条件、否定、指代、纠正、待澄清或历史复用时调用。意图与上下文共用每轮唯一一次异步调用；`ENABLE_LLM_CONTEXT_SEMANTICS=false` 时该调用不携带 active case、最近轮次、待澄清或候选摘要，也不采用模型的 context 提议。取消、超时或校验失败都会回退确定性 Canonical 链路。
- Ollama / FAISS / 知识库：`OLLAMA_BASE_URL`、`EMBEDDING_MODEL`、`FAISS_PATH`、`KB_CHUNK_SIZE`、`KB_CHUNK_OVERLAP`、`KB_BATCH_SIZE`、`KB_QUERY_TIMEOUT_SECONDS`、`KB_EMBED_TIMEOUT_SECONDS`、`KB_BUILD_MAX_DOCUMENTS`、`KB_INCREMENTAL_BUILD`、`KB_EMBED_CACHE_PATH`。
- 上传知识文件 / OCR：`ADMIN_UPLOAD_DIR`、`ADMIN_PDF_MAX_FILE_SIZE`、`PDF_TEXT_EXTRACT_BACKEND`、`DOCUMENT_OCR_BACKEND`、`DOCUMENT_OCR_LANG`、`DOCUMENT_OCR_MAX_PAGES`、`DOCUMENT_OCR_RENDER_DPI`、`PDF_TEXT_MIN_CHARS`、`PDF_TEXT_PREVIEW_CHARS`、`UPLOADED_FILE_KB_ENABLE_VECTOR_INDEX`、`UPLOADED_FILE_KB_VECTOR_TIMEOUT_SECONDS`。
- Artifact backend：`DIAGNOSIS_ARTIFACT_BACKEND=file|memory|postgres`、`DIAGNOSIS_ARTIFACT_DIR`、`DIAGNOSIS_ARTIFACT_TABLE`、`DIAGNOSIS_ARTIFACT_POSTGRES_DSN`。旧 `WORKFLOW_ARTIFACT_*` 仍作为 fallback 读取。
- PostgreSQL artifact/health：`POSTGRES_HOST`、`POSTGRES_PORT`、`POSTGRES_DB`、`POSTGRES_USER`、`POSTGRES_PASSWORD`。
- Session / auth：`SESSION_SECRET`、`SESSION_SECRET_FILE`、`SESSION_COOKIE_SECURE`、`SESSION_COOKIE_SAMESITE`、`SESSION_COOKIE_DOMAIN`、`SESSION_COOKIE_PATH`、`USER_STORE_PATH`、`ADMIN_USERNAME`、`ADMIN_PASSWORD`、`ALLOW_DEFAULT_ADMIN_PASSWORD`、`ADMIN_AUTH_MAX_AGE`。
- 语音身份：`VOICE_AUTH_SHARED_SECRET`、`VOICE_AUTH_MAX_AGE_SECONDS`。
- 资产注册表：默认读取 `config/asset_registry.json`，可用 `ASSET_REGISTRY_PATH` 指向其他 JSON 文件。它维护设备显示名/别名与数据库表、可选行级 `device_name` / `inverter_name` 的映射。
- 报告与本地状态：报告目录固定为 `trash/run/reports/`，由 `platform/paths.py` 的 `REPORTS_DIR` 定义；当前没有独立 `REPORTS_DIR` 环境变量。
- 审计与 trace：`SECURITY_AUDIT_PATH`、`AGENT_TRACE_BACKEND`、`AGENT_TRACE_CAPTURE_CONTENT`、`AGENT_TRACE_PREVIEW_CHARS`、`AGENT_TRACE_FLUSH_ON_RUN`、`AGENT_TRACE_FLUSH_TIMEOUT_SECONDS`、`AGENT_TRACE_LOCAL_LOG`、`AGENT_TRACE_LOCAL_LOG_PATH`、`AGENT_TRACE_CONSOLE`、`AGENT_TRACE_CONSOLE_VERBOSE`、`AGENT_TRACE_CONSOLE_PREVIEW_CHARS`、`LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY`、`LANGFUSE_HOST`、`LANGFUSE_BASE_URL`。
- 其他外部能力：`TTS_SYNTHESIZE_URL`、`TTS_SYNTHESIZE_TIMEOUT_SECONDS`、`TTS_SYNTHESIZE_MAX_CHARS`、`ASSET_REGISTRY_PATH`、`HISTORY_INDEX_PATH`、`WORKORDER_DIR`、`DCMA_SQL_TIME_ANCHOR=now|latest_row_if_stale`、`HEALTHCHECK_TIMEOUT_SECONDS`。`DCMA_SQL_TIME_ANCHOR` 生产默认 `now`；非生产默认 `latest_row_if_stale`，用于历史快照库在最近窗口为空时回退到最新采样窗口。

`LOCAL_DEV_MODE=true` 会跳过真实 MySQL/知识库初始化，走 `server/devtools/dev_mode.py` 的本地模拟流。

## 后端目录结构

```text
fault_diagnosis/
  app.py                  进程入口，创建 FastAPI app 并交给 server_runner
  config.py               兼容配置入口，转发到 platform/settings.py
  server/                 HTTP/SSE、auth、session、use cases、Agent gateway、bootstrap、devtools
  agent/                  V2 understanding、skill routing、planning、typed runtime、output projection
  domain/                 diagnosis/context/security 领域合同、规则和纯 helper
  platform/               persistence、tools、knowledge、integrations、observability、settings、paths
  shared/                 无业务语义的通用编码与小工具
```

扩展时保持分层：`server/http/routers` 只接 HTTP；`server/use_cases` 管会话、权限上下文、历史和停止流；`server/agent_gateway` 只做流协议、取消和错误分类；`agent/` 承担 V2 诊断执行；`domain/` 保存领域合同和规则；`platform/` 保存持久化、工具、知识库、外部集成和观测能力。

## HTTP / SSE 接口

聊天与 Agent：

- `GET /chat/stream`：主 SSE 入口。Query 参数为 `message`、可选 `thread_id`、兼容 `user_identity`、可选 `stream_id`。服务端根据 cookie/session 解析真实身份，并校验请求的 thread 是否属于当前 session。
- `GET /chat/stream/edit`：编辑指定用户轮次后重新生成，参数为 `message`、`thread_id`、`user_turn_index`、可选 `user_identity`、`stream_id`。
- `POST /chat/stop`：按 `stream_id` 停止当前 session 拥有的活跃流。
- `POST /agent/chat`：语音网关兼容 JSON 入口。它不复制 Agent 逻辑，而是复用 `/chat/stream` 同一条主链路，内部消费 SSE，把 `token` 和 `chat_complete` 聚合为 `reply_text`、`visual_actions`、`thread_id` 等 JSON。
- `GET /chat/plan`：受控调试接口，仅 `ENABLE_PLAN_ENDPOINT=true` 或 `LOCAL_DEV_MODE=true` 时启用。它返回 goal-native plan snapshot，包括 `resolved_context`、`goal_set`、`task_family`、`policy_id`、`enabled_nodes`、`runtime_tools`、`readiness`、`manual_confirmation`、`evidence_gaps`，不输出退役的 shadow/diff/gate 计划字段，也不改变真实执行。

认证与身份：

- `GET /auth/identity`
- `POST /auth/login`
- `POST /auth/admin/login`
- `POST /auth/dev-login`
- `POST /auth/voice/exchange`
- `POST /auth/logout`

报告：

- `GET /reports/{filename}`：私有报告访问入口。报告文件不作为公共静态目录直接挂载；文件名必须匹配安全白名单，普通用户还要通过 `.access.json` 校验设备与表范围，管理员可查看全部报告。

工单：

- `POST /api/workorders`
- `GET /api/workorders`
- `GET /api/workorders/{work_order_id}`
- `POST /api/workorders/update`

这些是人工确认后的工单应用接口，不由 Agent 自动调用。当前 `update` 不允许把状态改成派发或执行类状态。

知识库 / 管理 / 历史 / 健康：

- `GET /health/dependencies`、`GET /health/real`、`GET /health/ocr`
- `GET /ai/history/{type}`、`GET /ai/history/{type}/{chat_id}`、`DELETE /ai/history/{type}/{chat_id}`、`GET /api/todos/{thread_id}`
- `GET /admin/knowledge-files`、`POST /admin/knowledge-files`、`GET /admin/knowledge-files/{record_id}`、`GET /admin/knowledge-files/{record_id}/file`、`POST /admin/knowledge-files/{record_id}/ingest`、`PATCH /admin/knowledge-files/{record_id}/correction`、`DELETE /admin/knowledge-files/{record_id}`
- `POST /api/governance/save`、`GET /api/governance/list`、`POST /api/governance/ledger`、`GET /api/governance/ledger`、`POST /api/governance/ledger/update`
- `POST /tts/synthesize`
- `GET /`

## 主请求链路

```text
GET /chat/stream
  -> server session authentication and thread ownership
  -> ChatService / ProductionTurnCoordinator
  -> ConversationContextAssembler
  -> DeterministicClauseParser + SemanticResolutionService（每轮最多一次 async 调用）
  -> ConversationTurnCoordinator / CanonicalGoal construction
  -> ACL-filtered ContextCandidate projection / CanonicalContextBinder
  -> per-goal authorization / SourceResolver / Readiness
  -> PlanCompiler / PlanValidator
  -> WorkflowRuntimeExecutor / typed nodes
  -> EvidenceLedger / Artifact commit and verified readback / CaseState update
  -> OutputFrame / CompositePresenter / optional Grounded Answer
  -> SSE projection / chat_complete / trace export
```

`GET /chat/plan` 与 `/chat/stream` 都从同一个异步语义入口进入 Canonical 预览；plan-only 不再补发第二次
Shadow 调用。它不进入 Runtime、typed nodes、证据提交、Artifact 持久化、CaseState 更新、回答合成或 SSE 流。
因而 plan-only 请求不写业务 Artifact、不推进 CaseState，也不调用 Grounded Answer 模型。

意图和上下文使用同一个 `SemanticContextPacket`，每轮至多调用模型一次。输入只包含当前消息、确定性解析、
ACL 投影后的 active case、最近轮次安全摘要、待澄清缺槽、上下文候选和 capability 白名单。最近轮次不包含
完整聊天文本，只保存 capability、设备、故障码、deliverable 状态和限制说明；Artifact ID、candidate ID、
lineage、SQL、权限和执行节点不会进入该安全包。模型统一返回 `SemanticTurnProposal`，其中 clauses 交给
`IntentCanonicalizer`，context 交给 `ContextProposalValidator`；真实 Artifact 仍仅由
`CanonicalContextBinder` 在内部候选集中确定。

Feature Flag 组合：

| Grounded Answer | `LLM_SEMANTIC_MODE` | 行为 |
|---|---|---|---|
| off | off | 默认生产模式，完整确定性主链；旧 fallback 开关仍按兼容规则生效 |
| off | shadow | 每轮最多调用一次，仅记录模型提议与差异观测 |
| off | primary | 每轮最多调用一次；模型提议须经 span、资产注册表、格式与 capability 白名单的字段级 ACCEPT/REJECT/CLARIFY 仲裁，Planner 只读取最终 Canonical 投影 |
| on 且命中灰度 | 任意 | Runtime 完成后最多调用一次回答模型，只改最终表达；失败回退 CompositePresenter |

`POST /agent/chat` 进入 `ChatService.agent_chat` 后复用同一个 `token_stream_events`，只是在服务层把 SSE 聚合为 JSON。

职责边界：

- HTTP 层不直接做诊断业务。
- service 层负责 session/thread 解析、历史消息、身份上下文、停止流、语音聚合。
- `server/agent_gateway/` 负责 SSE 适配、取消句柄、错误分类和 dev mode 分流。
- `agent/` 负责 V2 understanding、skill routing、plan validation、typed nodes、output projection。
- `platform/persistence/diagnosis_artifacts/store.py` 保存线程级诊断产物，支撑后续“基于刚才结果生成报告”“是不是要生成工单”等续问。

## SSE 契约

常见事件序列：

```text
start -> task_update* -> ping* -> tool_start/tool_end* -> token -> complete
```

错误时可能返回 `server_error`。工具事件会带 `tool`、`input`、`result_preview`、`stage`、`run_id`，SQL 和知识库工具还会补充 evidence preview。

`complete` 中重点字段：

- `runtime`
- `final_content`
- `decision`
- `resolved_context`
- `goal_set`
- `task_family`
- `policy_id`
- `decision.enabled_nodes` / `workflow_route.enabled_nodes`
- `decision.runtime_tools` / `workflow_route.runtime_tools`
- `readiness`
- `diagnosis_readiness`
- `workorder_action_readiness`
- `manual_confirmation`
- `sql_artifact`
- `knowledge_artifact`
- `analysis_artifact`
- `permission_check`
- `risk_check`
- `resolution_recommendation`
- `workorder_decision`
- `audit_log`
- `report_artifact`
- `evidence_bundle`
- `output_guardrail`
- `artifact`
- `trace`
- `todos`
- `workflow_route`、`workflow_policy`、`workflow_result`、`workflow_envelope` 等兼容字段，如由 adapter 补齐

`decision` 中的旧任务类型、旧候选任务和旧意图列表如果出现，只是 V2 output projection 的前端兼容字段。前端和调试工具不应把它们理解为内部核心决策来源。新调试字段优先看 `resolved_context`、`goal_set`、`task_family`、`policy_id`、`decision.enabled_nodes`、`decision.runtime_tools`、`readiness` 和 `manual_confirmation`。

## 身份与权限

真实身份来源是服务端 session/cookie。`/chat/stream` 入参中的 `user_identity` 只用于兼容展示和提示，不参与授权。

语音身份有两种入口：

- `/agent/chat` 可使用 `X-Voice-User`、`X-Voice-Role`、`X-Voice-Timestamp`、`X-Voice-Nonce`、`X-Voice-Signature` 直连。
- 浏览器可先调 `/auth/voice/exchange`，用相同 HMAC 信息换取本后端 session/cookie。

签名使用 `VOICE_AUTH_SHARED_SECRET` 做 HMAC-SHA256，默认有效窗口由 `VOICE_AUTH_MAX_AGE_SECONDS` 控制。权限仍由服务端用户记录和角色策略生成。

权限模型是 RBAC + ABAC：

- `role`：`guest`、`engineer`、`admin`。
- `permissions`：workflow、tool、data、KB、admin 能力点。
- `asset_scope`：设备范围。
- `allowed_tables` / `table_scope`：可访问数据表。
- `system_scope`、`location_scope`、`kb_scopes`：系统、位置、知识库可见性。

权限检查覆盖：

- thread ownership 和历史访问。
- SQL 表访问、设备过滤、时间窗口和行数限制。
- RAG 文档可见性。
- 报告读取和报告生成。
- 工单创建、读取和状态更新边界。
- 每次受限工具调用。
- action/workorder 的 permission、risk、audit、manual confirmation。

典型边界：

- `guest`：默认只能看 `g120_motor_1` / `real_data_01` 最近窗口、公开知识库、状态类和故障码公开解释；不能生成报告、不能诊断根因、不能生成工单。
- `engineer`：可在授权设备/表范围内做诊断、报告、工单草稿创建；不能越权查看设备或表。
- `admin`：可访问全部业务表、报告、上传知识文件 和管理能力。

## SQL、知识库、报告与外部依赖

SQL：

- SQL safety helper 将 `SELECT` / `WITH` 识别为只读形态，但当前执行前的 `sql_acl.py` 只支持可安全重写的单表 `SELECT`，会拒绝 `WITH`、多语句、注释、`UNION`、`FOR UPDATE`、`INTO OUTFILE` 等超出安全重写能力的结构。
- 只允许白名单表：`real_data_01`、`real_data_02`、`real_data_03`、`device_alarm`、`device_metric`、`device_fault_data`、`fault_records`。
- 禁止旧表 `real_data` 和未知表。
- 当前/最近运行状态默认查 `real_data_01`；如果设备能在资产注册表解析，则优先使用绑定数据源。`real_data_01~03` 默认按“表级数据源”处理：表本身代表 G120 电机 1~3，不额外注入 `device_name` 过滤；如果某个表是多设备共表，可在对应 `data_sources` 中配置 `device_name` 或 `inverter_name`，ACL 和快速 SQL 会自动注入行级过滤。
- `build_fast_sql_plan()` 会为常见状态/报告请求生成确定性 SQL 并跳过 checker；否则使用 LLM 规划后仍经只读、表名和 ACL 校验。
- `apply_sql_acl()` 会结合 `allowed_tables`、`asset_scope`、角色时间窗口和 `LIMIT` 重写 SQL。访客仅 `real_data_01` 最近 1 小时；工程师受表和设备范围限制；管理员可访问授权业务表全集。

知识库：

- `query_knowledge_base` 先抽取故障码，在本地 PDF 文本中做精确匹配；不足时再查基础 FAISS 知识库，并合并上传知识文件 知识库结果。
- `rag_acl.py` 根据文档 `visibility`、`allowed_roles`、`allowed_asset_ids`、`allowed_systems` 过滤结果。
- 知识库只提供手册、SOP、故障码解释等证据，不代表实时设备状态；实时状态必须来自 SQL 或其他运行数据证据。

报告：

- `save_report` 写入 `trash/run/reports/`，并生成同名 `.access.json` 访问范围。
- 报告通过 `/reports/{filename}` 私有访问，不能当公共静态文件直接访问。
- 报告内容来自结构化 `operation_report_payload` 和图表 payload，报告 artifact 会进入 `complete` 和 diagnosis artifact。

运行态依赖：

- MySQL：运行数据和健康检查。
- OpenAI-compatible LLM：请求理解、SQL 规划降级、分析和最终回答。
- Ollama / FAISS：本地 PDF 知识库。
- PostgreSQL：可选 diagnosis artifact backend。
- 本地文件系统：报告、artifact、历史索引、用户文件、知识文件 registry、工单 mock、审计和 trace。

## Artifact 与上下文复用

诊断 artifact 默认保存到：

```text
trash/run/diagnosis_artifacts/*.jsonl
```

可选 backend：

- `file`：按 thread_id hash 分片写 JSONL。
- `memory`：测试或本地注入使用。
- `postgres`：写入 JSONB 表，表名默认 `diagnosis_artifacts`。

artifact 支撑多轮续问：

- “基于刚才结果生成报告”：`ContextResolver` 识别 `report_handoff`，从当前 thread 最近 artifact 映射报告输入。
- “是不是要生成工单”：识别 `action_followup`，复用上一轮 artifact，但必须检查 stale、权限、设备一致性和 evidence。
- “那 J2 呢”：显式设备切换，不能复用 J1 artifact，需要刷新 J2 数据或提示缺证据。
- “刚才那个故障码什么意思”：可继承上一轮故障码，但仍受权限和上下文歧义检查。

复用原则：

- 必须满足 thread、权限、设备、时间、artifact 类型、staleness 条件。
- 缺证据不能假装有证据。
- stale evidence 必须刷新或披露。
- 越权时不能继承上下文。
- 用户显式切换设备时不能复用旧设备 artifact。

## 开发与扩展约定

- 新接口放 `server/http/routers/`，用例编排放 `server/use_cases/`。
- 新持久化放 `platform/persistence/`，不要在路由里直接写业务文件。
- 新工具先在 `platform/tools/` 或 `domain/` 实现，再接入 V2 `ToolRuntime`/typed node、`domain/security/tool_gateway.py` 或对应 ACL、evidence 转换。
- 修改 V2 流程顺序改 `agent/planning/` 和 `agent/runtime/graph.py`；修改单个节点改 `agent/runtime/nodes/`。
- 修改 skill 路由改 `agent/understanding/`、`agent/skills/` 和 `agent/planning/`。
- 修改输出字段改 `agent/output/`、`agent/output/diagnosis_payload.py`、`domain/diagnosis/contracts.py`。
- 修改权限逻辑改 `domain/security/permissions.py`、`domain/security/policy_engine.py`、`domain/security/sql_acl.py`、`domain/security/rag_acl.py`、`domain/security/tool_gateway.py` 或 `server/http/routers/reports.py`。
- 修改报告模板改 `platform/tools/report_tools.py`、`agent/output/report.py` 和 `domain/diagnosis/reporting/` helper。
- 修改 RAG 逻辑改 `platform/knowledge/`、`platform/tools/kb_tools.py`、`domain/security/rag_acl.py`。
- 不要重新引入旧任务类型或旧意图列表作为内部 policy key。
- 不要恢复 shadow/diff/gate 双轨迁移逻辑。
- 不要让 LLM 自由选择工具或绕过 EvidenceBundle 下诊断结论。

## 验证命令

后端推荐：

```bash
PYTHONPATH=. pytest -q
PYTHONPATH=. python scripts/goal_native_cutover_check.py
PYTHONPATH=. python scripts/legacy_dependency_scan.py --json
PYTHONPATH=. python scripts/context_acceptance_test.py
PYTHONPATH=. python scripts/goal_acceptance_test.py
python -m compileall -q fault_diagnosis
git diff --check
```

涉及前端字段、前端展示或 `todos` 时再执行：

```bash
cd agent_fronted
npm run build
```

## 历史兼容说明

当前主链路已经是 Agent Engine V2。`workflow_route`、`workflow_policy`、`workflow_result`、`workflow_envelope` 以及旧任务类型/旧意图投影可能仍会出现在 SSE、artifact 或前端适配里，但它们是兼容输出字段，不是内部事实来源。

当前内部事实链是：

```text
CurrentUtteranceParse
  -> CanonicalTurnRequest
  -> per-goal authorization/readiness/source resolution
  -> validated ExecutionPlan DAG
  -> ArtifactRoleBinding / exact loading
  -> GoalExecutionResult
  -> DeliverableResult / composite output
```

`IntentFrame`、`RewriteFrame`、`EffectiveRequestFrame` 和 `SkillRoute` 只保留为兼容/debug 投影，不是生产事实来源。
