# single_agent 单 Agent 说明

`fault_diagnosis/single_agent/` 是 faultAgent 后端的限制型单 Agent 核心。它不是多 Agent 编排，不是 LangChain ReAct 式开放循环，也不是让 LLM 自由决定调用哪个工具。工具调用由固定阶段代码、goal-native policy、权限和硬白名单共同控制，主流程可审计、可复现、可限制。

诊断结论必须落到 `EvidenceBundle`；工单和设备动作只能输出建议、草稿或人工确认要求，不能自动执行设备控制，不能自动派发工单。

## Agent 定位与边界

外部入口不是阶段函数，而是：

```text
GET /chat/stream
  -> api/chat.py
  -> ChatService.stream_chat
  -> agent_runtime.streaming.token_stream_events
  -> RestrictedSingleAgentRunner.stream_events
  -> single_agent/flow.py
```

`POST /agent/chat` 语音兼容入口也复用同一条流，只在服务层聚合成 JSON。

Agent 层只负责一次诊断请求如何执行；HTTP、session、thread ownership、历史索引、数据库池和应用启动属于 `api/`、`services/`、`auth/`、`repositories/`、`infrastructure/`。

## 当前 goal-native 主链路

```text
user request
  -> understand_request
  -> ContextManager.resolve
  -> ResolvedContext
  -> build_goal_set
  -> GoalSet
  -> resolve_task_family
  -> select_policy_from_intent_axes
  -> resolve_nodes_from_goals
  -> readiness / manual_confirmation
  -> fixed stages
  -> EvidenceBundle
  -> final_answer
  -> output compat projection
  -> save artifact
```

内部不再以旧任务类型或旧意图列表为核心。旧任务类型、旧候选任务和旧意图列表只在 output/artifact/前端兼容边界生成。退役的 shadow/diff/gate 计划字段不再是当前生产链路。

核心目录：

```text
single_agent/
  runner.py                 runner 门面、模型调用、工具白名单、trace、工具 SSE
  flow.py                   顶层流式状态机和固定阶段顺序
  stages.py                 understand/sql/knowledge/analysis/report/final/save 等业务阶段
  intent.py                 请求理解 fallback、capability decision
  context.py                ArtifactBackedCaseStore 和 ContextManager 门面
  planner.py                /chat/plan 的 side-effect-free goal-native plan
  workflow/                 GoalSet、task_family、policy、node、todo 投影
  planning/                 diagnosis/workorder readiness 和人工确认合同
  evidence/                 EvidenceBundle 构建、SQL/KB evidence、Claim、质量校验
  output/                   complete payload、模板化输出、兼容字段投影
  reporting/                报告 payload、结构化运行报告、章节和指标 helper
  compat/                   单向 legacy 字段投影
  support/                  JSON、序列化、工具懒加载
  sql_safety.py             SQL schema、只读校验、fallback/fast plan
  sql_result_parser.py      SQL 输出解析
  artifacts.py              DiagnosisArtifactEnvelope 构建和保存
  workorder_suggestions.py  工单建议/草稿产物
```

`planning/` 中如果仍有历史 shadow/diff/gate 文件，只能按退役迁移遗留理解；当前执行链路使用的是 readiness 和 manual confirmation。

## 上下文管理

上下文合同定义在 `fault_diagnosis/context/contracts.py`：

- `ResolvedContext`：本轮请求对上一轮上下文的解析结果，包含 `relation_to_previous`、`referenced_artifact_id`、`inherited_slots`、`stale_evidence`、`missing_context`、`evidence_mode`、`should_refresh_runtime_data` 等。
- `CaseState`：从线程级 diagnosis artifact 投影出来的活动诊断 case，记录当前设备、故障码、报告、证据包、严重程度、数据新鲜度和 pending actions。
- `PendingAction`：上一轮 artifact 暗示的待处理动作，只表示“待确认动作”，不是已经执行。
- `ContextReference`：用户话语里对上一轮对象或显式对象的引用。
- `ArtifactBackedCaseStore`：位于 `single_agent/context.py`，从 `diagnosis.artifact_store.get_thread_artifact()` 读取当前 thread 最近 artifact，并投影成 `ConversationDiagnosisState`。
- `ContextResolver`：位于 `context/resolver.py`，判断本轮是否能复用上一轮设备、报告、故障码、运行数据和 pending action。

上下文关系包括 `new_case`、`report_handoff`、`action_followup`、`refresh_current_status`、`continuation`、`ambiguous`、`correction`。

复用原则：

- 可复用必须满足 thread、权限、设备、时间、artifact 类型和 staleness 条件。
- 缺证据时不能假装有证据。
- stale evidence 必须刷新或披露。
- 越权时不能继承上下文。
- 用户显式切换设备时不能复用旧设备 artifact。

典型续问：

- “基于刚才结果生成报告”：`report_handoff`，从上一轮 artifact 生成报告。
- “是不是要生成工单”：`action_followup`，复用上一轮诊断但检查 stale、权限和 evidence。
- “那 J2 呢”：显式新设备，不能复用 J1 artifact。
- “刚才那个故障码什么意思”：可继承故障码，但存在多个候选时进入 `ambiguous`。

## 意图拆解 / GoalSet

`GoalSet` 定义在 `workflow/contracts.py`，由 `workflow/goals.py::build_goal_set()` 确定性构造，不由 LLM 直接生成。LLM 或规则理解只提供初始 payload；GoalSet 根据请求文本、抽取对象、resolved context 和 route hint 生成。

`IntentGoal` 表示一个结构化目标，包含：

- `goal_id`
- `goal_type`
- `status`
- `depends_on`
- `required_slots`
- `missing_slots`
- `required_evidence`
- `expected_output`
- `risk_level`
- `source`
- `context_refs`
- `reason`

一个请求可以有多个 goal，也可以有依赖和 blocked 状态。例如：

- “A07089 是什么？现在设备有故障吗？要不要生成工单？”会拆出故障码解释、运行状态、严重性/诊断、工单判断。
- “生成报告，然后看看是否需要工单”会拆出报告和工单判断，后者受 evidence 和人工确认限制。
- “这个告警严重吗，怎么处理？”会结合上下文生成严重性评估和处置建议。

当前支持的 `goal_type`：

- `explain_fault_code`：解释故障码或告警码含义。
- `check_runtime_status`：查询当前/最近运行状态。
- `diagnose_fault`：判断并诊断故障。
- `assess_severity`：评估严重程度、影响或风险。
- `recommend_resolution`：给出处置建议。
- `generate_report`：生成或导出报告。
- `decide_workorder`：判断是否建议生成待确认工单草稿。
- `refresh_current_status`：刷新当前实时状态。
- `clarify_missing_context`：澄清缺失或歧义上下文。
- `answer_meta_question`：回答权限、身份或能力范围问题。

兼容投影中还可能出现 `create_workorder_draft`、`dispatch_workorder`，但当前 GoalSet builder 不主动生成这些 goal；高风险动作会被 manual confirmation 和权限边界拦住。

## task_family 与 policy

`task_family` 是 goal-native 粗粒度任务族，不是旧任务类型。它用于 policy 选择、观测和调试。

当前取值：

- `knowledge_lookup`：知识库/故障码解释。
- `runtime_status`：当前或最近运行状态。
- `diagnosis`：故障诊断、告警分诊、根因、健康评估。
- `reporting`：报告生成。
- `action_or_workorder`：工单或高风险动作请求。
- `meta`：权限、身份、澄清等元问题。

policy selection 输入是 GoalSet goal types、`task_family`、`resolved_context`、`requested_output`、`action_target`、`action_type`、readiness/manual confirmation 相关轴。稳定 policy id 在 `workflow/policies.py`：

- `status_query_v1`
- `alarm_triage_v1`
- `fault_diagnosis_v1`
- `root_cause_analysis_v1`
- `health_assessment_v1`
- `knowledge_qa_v1`
- `report_generation_v1`
- `action_request_v1`
- `permission_scope_query_v1`

`select_policy_from_intent_axes()` 只根据 goal-native 轴选 policy，不存在 legacy policy fallback。`resolve_nodes_from_goals()` 从 goal 和 task family 补齐 node 需求；`build_workflow_plan()` 解析 `enabled_nodes` 和 `runtime_tools`。

`runtime_tools` 由启用节点映射得到：

- `sql` -> `sql_db_query_checker`、`sql_db_query`
- `knowledge` -> `query_knowledge_base`
- `report` -> `save_report`

实际工具调用还会经过 runner 硬白名单和 `security/tool_gateway.py` 权限校验。

## 固定阶段流程

当前主流程在 `flow.py`：

```text
start
  -> understand
  -> access_authorization
  -> select_workflow_policy
  -> initialize_evidence_bundle
  -> permission_check              按 enabled_nodes 可选
  -> risk_check                    按 enabled_nodes 可选
  -> workorder_decision            工单 artifact 续问快路径可提前进入
  -> report                        report_handoff 快路径可提前进入
  -> sql                           启用则执行，否则 skipped artifact
  -> knowledge                     启用或 SQL 发现故障码时执行，否则 skipped artifact
  -> analysis
  -> resolution_recommendation     按 enabled_nodes 可选
  -> workorder_decision            启用则生成建议，否则 skipped suggestion
  -> report                        启用则生成 HTML，否则 skipped report artifact
  -> evidence_validation
  -> final_answer
  -> output_guardrail
  -> audit_log                     动作/工单请求可选
  -> save_artifact
  -> token
  -> complete
```

轻量问候和能力询问走直接回复快路径：`start -> final_answer -> token -> complete`。

阶段说明：

| 阶段 | 输入 | 输出 | 可跳过 | LLM | 工具 | EvidenceBundle |
| --- | --- | --- | --- | --- | --- | --- |
| `understand` | message、history/context、auth | `DiagnosisRequest`、`SingleAgentDecision` | 否 | 可能，规则 fallback 可替代 | 否 | 记录用户请求基础 |
| `access_authorization` | auth、decision | authorization、access_scope | 否 | 否 | 否 | 防止越权证据进入后续 |
| `select_workflow_policy` | GoalSet、task_family、context | policy、enabled_nodes、runtime_tools | 否 | 否 | 否 | 决定后续 evidence 需求 |
| `initialize_evidence_bundle` | request、decision | 空 `EvidenceBundle` | 否 | 否 | 否 | 初始化账本 |
| `permission_check` | action/workorder decision | permission_check artifact | 可 | 否 | 否 | 高风险边界证据 |
| `risk_check` | action/workorder decision | risk_check artifact | 可 | 否 | 否 | 高风险边界证据 |
| `sql` | request、policy、auth scope | `SqlStepArtifact` | 可 | 可能用于 SQL 规划，fast plan 可跳过 | `sql_db_query_checker`、`sql_db_query` | 运行数据 evidence |
| `knowledge` | request、SQL fault codes | `KnowledgeStepArtifact` | 可 | 否 | `query_knowledge_base` | 手册/故障码 evidence |
| `analysis` | SQL、KB、request | `AnalysisStepArtifact` | 否 | 是 | 否 | 生成判断基础 |
| `resolution_recommendation` | decision、analysis | recommendation artifact | 可 | 否 | 否 | 处置建议 evidence |
| `workorder_decision` | request、SQL、KB、analysis 或上一轮 artifact | `WorkOrderSuggestion` | 可 | 否 | 否 | 工单建议 claim |
| `report` | SQL、KB、analysis、workorder | `ReportStepArtifact` | 可 | 否 | `save_report` | 报告 artifact |
| `evidence_validation` | 所有阶段产物 | 完整 `EvidenceBundle`、quality checks | 否 | 否 | 否 | 核心证据链 |
| `final_answer` | analysis、report、decision、evidence | 用户回答 | 否 | 可能，模板 fallback | 否 | 引用 evidence |
| `output_guardrail` | final answer、EvidenceBundle、decision | guardrail result / safe rewrite | 否 | 否 | 否 | 校验 claim 和危险话术 |
| `audit_log` | action/workorder artifacts | audit artifact | 可 | 否 | 否 | 审计信息 |
| `save_artifact` | 全部产物 | `DiagnosisArtifactEnvelope` | 否 | 否 | artifact store | 保存可复用上下文 |

前端进度不是完整阶段列表，而由 `workflow/todos.py` 投影成少量分组。

## 工具调用与安全边界

Runner 硬限制在 `contracts.py::SingleAgentLimits`：

```text
max_rounds = 18
max_tool_calls = 4
allowed_tools = (
  "sql_db_query_checker",
  "sql_db_query",
  "query_knowledge_base",
  "save_report",
)
```

工具调用统一走 `RestrictedSingleAgentRunner._invoke_restricted_tool()`：先检查硬白名单、本轮 `runtime_tools`、`max_tool_calls` 和 `authorize_tool_call()`，再写 trace、发 `tool_start`，执行工具，最后写 `tool_end` 和 evidence preview。

当前工具：

| 工具 | 阶段 | 输入 | 输出 | 权限与安全 |
| --- | --- | --- | --- | --- |
| `sql_db_query_checker` | `sql` | `{"query": sql}` | checker 返回 SQL 文本 | 必须有 `tool.sql.read`，且仍要只读/表名/ACL 复检；fast plan 可跳过 |
| `sql_db_query` | `sql` | `{"query": sql}` | SQL 结果文本，写入 `SqlStepArtifact` | 只执行阶段生成并校验过的只读 SQL；结合 `allowed_tables`、设备范围和时间窗口 |
| `query_knowledge_base` | `knowledge` | `{"query": query}` | 故障码/手册/SOP 片段，写入 `KnowledgeStepArtifact` | 必须有 `tool.kb.search`；RAG 结果按角色、资产、系统可见性过滤 |
| `save_report` | `report` | `report_filename`、`chart_payload`、`operation_report_payload` | HTML 报告文件和访问 URL，写入 `ReportStepArtifact` | 必须有 `tool.report.write_draft`；只能写报告目录；访问仍经 `/reports/{filename}` 权限校验 |

模型不直接自由调用工具。SQL 是只读；报告只写 `trash/run/reports/`；知识库不是实时状态来源；工单建议不等于已创建或已派发。

## EvidenceBundle 与输出可信度

数据模型在 `diagnosis/contracts.py`：

- `EvidenceItem` = 事实证据，描述来源、内容、质量和元数据。
- `Claim` = 基于证据形成的判断，必须引用 `supporting_evidence_ids`。
- `EvidenceBundle` = 本轮事实与判断账本，包含 task、evidence、claims、quality checks 和关联产物。

证据来源：

- 用户请求：`ev_user_request`
- SQL 结果：运行状态、样本窗口、告警事件、指标快照、时序特征、缺失运行数据
- 知识库片段：故障码、手册、SOP、适用范围
- 分析结果：诊断摘要、根因候选、风险评估、建议
- 工单建议：是否建议工单、优先级、验收标准
- 报告 artifact：报告文件名、报告生成状态

质量检查由 `evidence/quality.py::validate_evidence_bundle()` 生成，当前字段包括：

- `has_asset`
- `has_user_request`
- `has_current_status`
- `has_alarm_history`
- `has_manual_reference`
- `has_timeseries_feature`
- `all_claims_have_evidence`
- `no_dangling_evidence_refs`
- `dangling_evidence_refs`
- `missing_evidence_disclosed`
- `evidence_count`
- `claim_count`
- `no_unauthorized_evidence_refs` 由 flow 在授权后补充

`output_guardrail` 防止：

- 空回答。
- 声称已执行重启、停机、关闭告警、复位、改参数、派发等高风险动作。
- claim 缺少支持证据。
- 引用不存在的 evidence id。
- 权限降级时未披露权限限制。
- stale artifact 没有刷新或披露。

## Readiness / Manual Confirmation / 工单与动作边界

`planning/action_readiness.py` 定义 `WorkorderActionReadiness`：

- `ready_for_draft`
- `action_type`: `workorder_decision`、`workorder_draft`、`device_action`、`unknown`
- `requires_human_confirmation`
- `permission_check_required`
- `risk_check_required`
- `audit_log_required`
- `output_guardrail_required`
- `stale_refresh_required`
- `missing_critical_evidence`
- `blockers`

`planning/manual_confirmation.py` 定义 `ManualConfirmationRequirement`：

- `required`
- `confirmation_type`: `workorder_draft`、`dispatch`、`reset`、`stop_machine`、`parameter_change`、`unknown`
- `required_role`: `engineer`、`admin`、`unknown`
- `allowed_next_step`: `draft_only`、`ask_confirmation`、`refresh_data_first`、`deny`
- `forbidden_phrases`

边界规则：

- `draft_only`：只允许形成待确认工单草稿建议。
- `ask_confirmation`：可以询问人工确认，但不能代替确认执行。
- `refresh_data_first`：上一轮 evidence stale，必须先刷新或披露。
- `deny`：设备控制、派发、停机、复位、改参数等直接执行请求必须拒绝或转人工审批。

必须明确：

- 不自动派发工单。
- 不自动重启设备。
- 不自动复位。
- 不自动停机/启停。
- 不自动修改参数。
- 工单建议只表示“建议创建/草稿”，不是已经创建。
- evidence stale 时要刷新或明确提示。
- 权限不足时不能继续工单或动作流程。

## 输出与兼容字段

`output/payloads.py` 构建 `complete`。推荐消费的新字段：

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
- `evidence_bundle`
- `output_guardrail`

旧兼容字段：

- 旧主任务类型投影
- 旧候选任务类型投影
- 旧意图列表投影
- `workflow_route`
- `workflow_policy`
- `workflow_result`
- `workflow_envelope`

旧字段只用于前端、历史 artifact 和输出模板兼容，不用于内部决策。`compat/legacy_intent.py` 的职责是从新结构单向投影旧字段，不能反向修改 GoalSet、route 或 policy，也不再用于内部 fallback。

## `/chat/plan` 调试说明

`/chat/plan` 是受控调试接口，仅在 `ENABLE_PLAN_ENDPOINT=true` 或 `LOCAL_DEV_MODE=true` 时可用。它仍复用服务端 session/auth，不信任前端身份。

输出 schema 为 `agent_plan_snapshot.v2`，包含：

- `resolved_context`
- `goal_set`
- `task_family`
- `policy_id`
- `workflow_policy`
- `enabled_nodes`
- `skipped_nodes`
- `planned_tools`
- `runtime_tools`
- `readiness`
- `manual_confirmation`
- `missing_slots`
- `evidence_gaps`
- `authorization`

它不输出退役的 shadow/diff/gate 计划字段，也不改变真实执行。

## 典型场景走读

`A07089 是什么意思？`

```text
explain_fault_code -> task_family=knowledge_lookup -> policy=knowledge_qa_v1
  -> knowledge -> analysis -> evidence_validation -> final_answer
```

预期查知识库，不查 SQL；回答要说明手册证据边界，不声称当前设备实时状态。

`J1号机当前状态怎么样？`

```text
check_runtime_status -> task_family=runtime_status -> policy=status_query_v1
  -> sql -> analysis -> evidence_validation -> final_answer
```

预期查 SQL，披露数据窗口、样本时间和 freshness。

`生成J1号机运行报告`

```text
generate_report -> task_family=reporting -> policy=report_generation_v1
  -> sql/knowledge as needed -> analysis -> report -> save_artifact
```

预期生成私有 HTML 报告，保存 report artifact 和 diagnosis artifact。

报告后追问 `从结果来看是不是要生成工单？`

```text
action_followup -> decide_workorder
  -> check referenced artifact / stale / auth / device
  -> workorder_decision -> evidence_validation -> final_answer
```

预期可复用上一轮 artifact，但只给建议或草稿边界。

设备切换 `那J2呢？`

```text
explicit new device -> relation=new_case/correction
  -> refresh J2 data or disclose missing evidence
```

不能复用 J1 artifact。

高风险动作 `帮我重启设备`

```text
action_or_workorder -> policy=action_request_v1
  -> permission_check -> risk_check -> manual_confirmation.allowed_next_step=deny
```

预期拒绝自动执行，提示需要人工审批/现场确认。

guest 权限不足：

```text
auth role=guest -> policy_engine denies/degrades
  -> no report/workorder/root-cause diagnosis
```

不能通过历史上下文绕过权限。

## 开发扩展指南

- 新增 `goal_type`：改 `workflow/contracts.py`、`workflow/goals.py`、`workflow/axes.py`，再补 policy 和测试。
- 新增 `task_family`：改 `workflow/contracts.py`、`workflow/task_family.py`、policy 选择和授权映射。
- 新增 `policy_id`：改 `workflow/policies.py` 的 registry、`_policy_id_from_axes()` 和节点解析。
- 新增 `enabled_node`：改 policy、`resolve_nodes_from_goals()`、`workflow_node_enabled()` 使用点、`flow.py` 阶段位置和 `workflow/todos.py` 投影。
- 新增工具：实现工具，加入 `SingleAgentLimits.allowed_tools`、`security/tool_gateway.py`、policy runtime tool 映射、stage 调用和 evidence preview。
- 新增 evidence 类型：改 `diagnosis/contracts.py`、`single_agent/evidence/*`、`evidence/quality.py` 和输出模板。
- 修改最终回答模板：改 `output/templates.py`、`output/renderers.py` 或 `final_answer.py`。
- 修改报告模板：改 `tools/report_tools.py` 和 `single_agent/reporting/`。
- 修改权限边界：改 `security/permissions.py`、`policy_engine.py`、`sql_acl.py`、`rag_acl.py`、`tool_gateway.py`。
- 修改 `/chat/plan` 输出：改 `single_agent/planner.py`。
- 修改前端兼容字段：改 `output/payloads.py`、`compat/legacy_intent.py`、`runtime/diagnosis_contract_adapter.py`。

禁止事项：

- 不要重新引入旧任务类型作为内部 policy key。
- 不要让旧意图列表决定节点启停。
- 不要恢复 shadow/diff/gate 双轨迁移。
- 不要让 LLM 自由选择工具。
- 不要绕过 EvidenceBundle 直接下诊断结论。
- 不要自动执行设备控制或派发工单。

## 验证建议

README 更新后建议执行：

```bash
PYTHONPATH=. pytest -q
PYTHONPATH=. python scripts/goal_native_cutover_check.py
PYTHONPATH=. python scripts/legacy_dependency_scan.py --json
python -m compileall -q fault_diagnosis
git diff --check
```

涉及前端字段或展示时再执行：

```bash
cd agent_fronted
npm run build
```
