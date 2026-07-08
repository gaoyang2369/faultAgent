# 当前架构总览

faultAgent 当前是工业设备故障诊断系统，后端源码根是 `fault_diagnosis/`，前端是 `agent_fronted/`。后端默认主链路已经切到 Agent Engine V2，不是多 Agent 编排，也不是开放式 autonomous agent loop。旧 `single_agent` runner 仅作为一个迭代周期内的 `AGENT_ENGINE_VERSION=legacy` 全局回滚路径保留。

## 主链路

```text
GET /chat/stream
  -> api/chat.py
  -> ChatService.stream_chat
  -> agent_runtime.streaming.token_stream_events
  -> AgentEngineV2.plan_only
  -> WorkflowRuntimeExecutor
  -> agent_engine.output SSE/artifact projection
  -> diagnosis artifact save
```

Agent 内部核心链路：

```text
user request
  -> IntentFrame
  -> ContextFrame
  -> RewriteFrame
  -> SkillRoute
  -> ExecutionPlan validation
  -> V2 typed runtime nodes
  -> EvidenceLedger
  -> OutputFrame
  -> SSE/artifact compat projection
  -> save artifact
```

内部事实来源优先看：

- `IntentFrame`
- `ContextFrame`
- `RewriteFrame`
- `SkillRoute`
- `ExecutionPlan`
- typed node results
- `EvidenceLedger`
- V2 artifacts
- `OutputFrame`

`workflow_*`、旧任务类型和旧意图字段只作为 SSE、artifact 和前端兼容投影存在，由 V2 output 层单向生成，不驱动 V2 skill 路由、计划、节点启停或 readiness。

## 后端分层

```text
api/             HTTP / SSE 路由
services/        应用服务、session/thread/history/stop stream 编排
auth/            session、cookie、thread ownership、voice exchange
security/        RBAC / ABAC、SQL/RAG/report/workorder/tool 权限
agent_runtime/   SSE 编码、流调度、取消、错误分类、V2/legacy 回滚选择
agent_engine/    V2 understanding、skill routing、planning、runtime、output projection
single_agent/    短期 legacy rollback；部分 SQL/report/evidence helper 被 V2 复用
context/         ResolvedContext、CaseState、PendingAction
diagnosis/       领域合同、artifact store、report mapper
tools/           SQL、知识库、报告工具
knowledge/       FAISS / Ollama / PDF 知识库
repositories/    用户、历史、知识文件 registry、治理、工单持久化
runtime/         dev mode、session namespace、前端兼容适配
infrastructure/  app 生命周期、数据库池、模型、CORS、静态资源
```

HTTP 层不做诊断业务；service 层不做 Agent 阶段逻辑；`agent_runtime/` 不做领域判断；`agent_engine/` 不管理 Web 会话和持久化仓储。`single_agent/flow.py` 不再是默认主链路。

## V2 Runtime

常规请求由 V2 plan 编译为 typed nodes：

```text
start
  -> task_update
  -> sql / rag / kg / analysis / report / workorder / approval
  -> EvidenceLedger validation/projection
  -> OutputFrame
  -> token
  -> complete
```

节点是否出现由 `SkillRoute` 和 `ExecutionPlan` 决定。缺设备、缺报告来源、权限不足或高风险动作由 V2 clarification/blocked/error 输出表达，不隐式回落到 legacy。

当前 typed nodes：

- `sql`：受 SQL ACL 保护的只读运行数据查询。
- `rag`：知识库检索，受 RAG ACL 和当前 `AuthContext` 约束。
- `kg`：KG 未配置时显式 skipped。
- `analysis`：基于结构化 SQL/KB artifact 生成诊断分析。
- `report`：只消费结构化 reportable payload 或 current artifacts。
- `workorder`：只生成建议/草稿，不派发。
- `approval`：高风险动作和工单边界阻断。

## 权限模型

真实授权来源是服务端 session / cookie 解析出的 `AuthContext`。前端 `user_identity` 不参与授权。

权限模型：

- `role`: `guest`、`engineer`、`admin`
- `permissions`: workflow、tool、data、KB、admin 能力点
- `asset_scope`: 设备范围
- `table_scope`: 可访问表
- `system_scope` / `location_scope`
- `kb_scopes`: 知识库可见性

检查覆盖：

- thread ownership
- SQL table / asset / time window / row limit
- RAG 文档可见性
- report write/read
- workorder create/read/update
- restricted tool call
- high-risk action manual confirmation

典型边界：

- `guest` 只能做受限状态查询和公开知识库查询，不能生成报告、工单或根因诊断。
- `engineer` 在授权设备和数据表范围内诊断、报告和创建待派单工单。
- `admin` 可访问全部业务表、报告和管理员知识文件能力。

## 工具与外部依赖

当前工具白名单：

- `sql_db_query_checker`
- `sql_db_query`
- `query_knowledge_base`
- `save_report`

运行依赖：

- MySQL：运行数据。
- OpenAI-compatible LLM：请求理解、SQL 规划 fallback、分析和最终回答。
- Ollama / FAISS：PDF 知识库。
- PostgreSQL：可选 diagnosis artifact backend。
- 本地文件系统：报告、artifact、用户文件、历史索引、知识文件 registry、工单 mock、审计和 trace。

SQL 当前只执行可安全重写的单表只读查询，白名单表为 `real_data_01`、`real_data_02`、`real_data_03`、`device_alarm`、`device_metric`、`device_fault_data`、`fault_records`。

知识库只提供手册证据，不代表实时设备状态。实时状态必须来自 SQL 或其他运行数据证据。

报告写入 `trash/run/reports/`，通过受保护的 `/reports/{filename}` 读取。

## Artifact 与多轮上下文

诊断 artifact 默认保存到：

```text
trash/run/diagnosis_artifacts/*.jsonl
```

可选 backend：

- `file`
- `memory`
- `postgres`

artifact 支撑这些续问：

- “基于刚才结果生成报告”
- “是不是要生成工单”
- “刚才那个故障码什么意思”
- “那 J2 呢”

复用条件：

- thread 一致
- 权限范围允许
- 设备没有被用户显式切换
- evidence 不 stale，或者能刷新/披露
- artifact 类型能支持本轮请求

越权、设备切换、上下文歧义和 stale evidence 都不能静默继承上一轮结果。

## 高风险动作边界

工单和设备动作不能自动执行：

- 不自动派发工单。
- 不自动重启设备。
- 不自动复位。
- 不自动停机/启停。
- 不自动修改参数。
- 工单建议只表示建议创建或草稿，不表示已经创建或已派发。

相关结构：

- `WorkorderActionReadiness`
- `ManualConfirmationRequirement`
- `allowed_next_step`: `draft_only`、`ask_confirmation`、`refresh_data_first`、`deny`

## 文档边界

详细后端说明见 [fault_diagnosis/README.md](../fault_diagnosis/README.md)。

legacy rollback 说明见 [fault_diagnosis/single_agent/README.md](../fault_diagnosis/single_agent/README.md)。
