# Agent Engine V2：当前 Agent 逻辑说明

本文只说明 `fault_diagnosis/agent/` 当前已经落地的 Agent Engine V2，以及它与上下文、权限、工具、持久化和 SSE 层的协作方式。

先给出最重要的结论：当前系统是一个**受限、可审计、按计划执行的单 Agent**。它不是多 Agent，也不是开放式 ReAct。模型不能在运行时随意循环、随意选工具或绕过权限。一次请求会先被拆成结构化语义，再经过权限、Skill 路由和计划校验，最后只执行校验后 DAG 中的 typed node。

当前主链可以概括为：

```text
用户消息 + 服务端身份 + thread_id
              │
              ▼
       请求理解 IntentFrame
              │
              ▼
     线程上下文 ContextFrame
              │
              ▼
  有效请求 EffectiveRequestFrame
              │
              ├── 能力级预授权：拒绝 / 降级 / 放行
              ▼
       查询重写 RewriteFrame
              │
              ▼
       Skill 路由 SkillRoute
              │
              ▼
   候选计划 ExecutionPlan
              │
              ├── workflow / asset / table / tool / risk 校验
              ▼
       已校验的有界 DAG
              │
              ▼
 SQL / RAG / KG / Analysis / Report / Workorder 等节点
              │
              ▼
 EvidenceLedger + Artifacts + NodeResults
              │
              ▼
 OutputFrame -> SSE complete -> 线程级 diagnosis artifact
```

## 1. 它解决什么问题

Agent 的目标不是“尽量多调用工具”，而是在工业故障诊断场景中回答四个问题：

1. 用户本轮真正想做什么，指向哪个设备、故障码、时间范围或上一轮产物？
2. 当前身份是否允许做这件事、读取这些设备和数据、调用这些工具？
3. 为得到可靠结论，需要执行哪些有类型、有依赖的步骤？
4. 最终结论由哪些证据支持，哪些信息缺失或已经过时？

因此，V2 的核心不是一个“大 prompt”，而是一组稳定合同：

```text
IntentFrame
  -> ContextFrame
  -> EffectiveRequestFrame
  -> RewriteFrame
  -> SkillRoute
  -> ExecutionPlan
  -> NodeResult / EvidenceLedger
  -> OutputFrame
```

这些合同集中定义在 [`contracts.py`](./contracts.py)。每一层只回答自己的问题，避免把意图理解、权限、工具调用和回答渲染堆在一个文件中。

## 2. 真实入口与完整生命周期

`agent/` 不直接接 HTTP。生产 SSE 请求的入口在：

```text
GET /chat/stream
  -> server/http/routers/chat.py
  -> server/use_cases/chat_service.py
  -> server/agent_gateway/streaming.py::token_stream_events
  -> AgentEngineV2.build_plan_snapshot
  -> prepare_v2_execution_validation
  -> WorkflowRuntimeExecutor(real_tools=True)
```

`streaming.py` 会先从服务端 session 得到 `AuthContext`，再把 `raw_message`、`thread_id`、`request_id`、`conversation_context` 和身份交给 [`engine.py`](./engine.py)。用户参数中的展示身份不能代替服务端授权身份。

一次正常请求经历三个阶段。

### 2.1 规划快照阶段

`AgentEngineV2.build_plan_snapshot()` 依次执行：

1. `IntentFrameBuilder`：识别意图、设备、故障码、时间窗口、输出诉求和风险提示。
2. `ContextFrameAdapter`：从当前线程历史 artifact 和近期对话信号解析可复用上下文。
3. `EffectiveRequestBuilder`：把“本轮明说的内容”和“允许继承的内容”合并成唯一有效请求。
4. `authorize_capability_preflight`：在进一步规划前做能力级授权。
5. `RewriteFrameBuilder`：生成面向用户、RAG、SQL 和 KG 的不同查询表达。
6. `SkillRouter`：选择本轮最小 Skill 集合。
7. `PlanCompiler`：根据 Skill 合同生成候选 `ExecutionPlan`。
8. `PlanValidator`：校验权限、资源范围、工具、节点、风险、审批和输出合同。

这一阶段原则上不执行 SQL、知识库检索或报告写入，产物是可检查的 `PlanSnapshotV2`。

如果能力预授权或计划校验返回 `blocked`，请求会直接投影为拒绝结果，不进入真实运行时。

### 2.2 运行前准备阶段

[`runtime/plan_preparer.py`](./runtime/plan_preparer.py) 给已经通过第一轮校验的计划补齐真实运行输入，例如：

- 为 SQL 节点根据设备注册表和时间范围生成受限查询；
- 为 RAG 节点写入检索 query、策略和上一轮 knowledge artifact 引用；
- 为报告节点装配上一轮可报告 artifact，或标记从本轮 runtime artifacts 构建；
- 为工单节点补充目标 artifact、诊断摘要、证据时效和 `draft_only` 边界。

补齐后再次调用 `PlanValidator(require_runtime_inputs=True)`。也就是说，“规划时合法”不代表可以直接执行，运行输入仍要再校验一次。

### 2.3 DAG 执行与输出阶段

[`runtime/executor.py`](./runtime/executor.py) 把 `ExecutionPlan` 交给 [`runtime/graph.py`](./runtime/graph.py)：

- 没写 edge 时按节点顺序串行连接；
- 写了 edge 时按依赖建立 DAG；
- 拒绝不存在的节点引用、自环、重复 `node_id` 和环形依赖；
- 按拓扑顺序执行；
- 父节点未完成时，子节点标记为 `skipped`；
- 节点可按 `retry` 配置重试；
- 收到取消信号后，当前及剩余节点进入取消路径；
- `blocked` 或最终失败会停止后续执行。

真实聊天入口使用 `WorkflowRuntimeExecutor(real_tools=True)`。不传 `real_tools=True` 时是测试用的确定性 fake node，不应把 fake 结果当成生产诊断。

运行完成后，系统会：

1. 固化证据账本；
2. 构建 `OutputFrame`；
3. 投影 SSE `token` / `complete`；
4. 生成前端兼容字段；
5. 保存线程级 diagnosis artifact；
6. 写入 canonical trace，供审计和排错。

## 3. “意识拆解”是如何做的

这里的“意识”不是一个不可见的长思维链，而是几个可观测、可校验的结构化层。每层都保留输入、判断结果和必要的 trace 摘要。

### 3.1 IntentFrame：只理解本轮字面请求

[`understanding/intent_frame.py`](./understanding/intent_frame.py) 负责：

- 规范化原始消息；
- 抽取设备引用，如 `J1号机`；
- 抽取故障码，并排除 `G120`、`S120` 等设备型号误识别；
- 抽取“当前、最近、昨天、近 N 小时”等时间范围；
- 识别子意图，如状态查询、故障解释、诊断、根因、报告、工单、设备动作；
- 识别高风险动作和缺失设备等歧义；
- 按风险优先级选出 `primary_intent`。

`IntentFrame` 不做授权，也不直接选择工具。比如用户说“重启 J1 号机”，这里会识别 `device_action_request` 和高风险提示，但不会真的生成设备控制调用。

当前生产调用没有向 `IntentFrameBuilder` 传入模型结果，因此主要走**确定性规则 fallback**。代码保留 `model_result` 合并入口，但这不等于当前生产链已让 LLM 主导意图识别。

### 3.2 ContextFrame：判断与上一轮是什么关系

[`context/resolver_adapter.py`](./context/resolver_adapter.py) 把 `domain/context` 的解析结果转换成 V2 `ContextFrame`。关系常见值包括：

- `new_case`：新问题；
- `continuation`：继续上一轮；
- `report_handoff`：基于上一轮结果生成报告；
- `action_followup`：基于上一轮诊断询问工单或动作；
- `refresh_current_status`：要求刷新当前状态；
- `correction`：用户明确切换或修正设备；
- `ambiguous`：有多个候选对象，无法安全继承。

它不会简单地把全部聊天历史拼进 prompt，而是从线程级 artifact 投影出 `CaseState`，再按槽位继承：设备、故障码、时间范围、artifact id、evidence bundle、报告、数据窗口和 freshness 等。

### 3.3 EffectiveRequestFrame：形成唯一可执行语义

[`context/effective_request.py`](./context/effective_request.py) 解决“用户这句话最终到底指什么”：

- 保留原始语义和原始动作；
- 合并当前显式槽位与允许继承的槽位；
- 记录每个槽位来源 `slot_sources`；
- 绑定目标 artifact / report / evidence bundle；
- 确定 `evidence_policy` 和时效披露要求；
- 在目标不唯一时生成澄清问题；
- 产出 `effective_semantic_intent`，供后续授权、路由和执行共同使用。

这个 Frame 很重要，因为后续不能各自猜一次用户意图。Skill 路由、计划节点输入和最终 `executed_semantic_intent` 都应围绕同一个有效请求。

### 3.4 RewriteFrame：按下游用途重写

[`understanding/rewrite.py`](./understanding/rewrite.py) 不覆盖原始问题，而是生成：

- `user_rewrite`：面向本轮任务的清晰表达；
- `retrieval_queries`：知识库检索问题；
- `sql_question`：运行数据查询目标；
- `manual_query`：手册检索目标；
- `kg_query`：知识图谱查询目标；
- `staleness_note`：报告或工单前的时效提醒。

例如“把刚才结果出个报告”会被重写成“基于当前线程上一轮诊断结果生成报告”，同时保留引用的 artifact，而不是误当成一个没有诊断材料的新报告请求。

## 4. 上下文管理：保存什么、复用什么、拒绝什么

上下文管理的核心原则是：**前端聊天历史不等于 Agent 上下文；Agent 主要复用经过结构化保存的线程 artifact。**

### 4.1 上下文来源

Agent 可用的上下文来自三处：

1. 当前消息中明确出现的设备、故障码、时间范围；
2. 服务层提供的近期对话信号，例如指代、纠正和开放续问；
3. 当前 `thread_id` 下已保存的 `DiagnosisArtifactEnvelope`，由 `ArtifactBackedCaseStore` 投影为 `CaseState`。

Artifact manifest 会记录每个产物是否可续问、可报告、可操作，以及它关联的设备、故障码、时间窗、证据、风险和来源节点。这样续问复用的是结构化事实，不是盲目复述上一条自然语言回答。

### 4.2 允许继承的情况

典型安全复用包括：

- “它现在还异常吗”继承唯一明确的上一轮设备；
- “详细点”继承上一轮唯一故障码和 knowledge artifact；
- “基于刚才结果生成报告”引用上一轮 reportable artifact；
- “要不要生成工单”引用上一轮分析、报告和证据 bundle。

复用结果会写入 `inherited_slots`，并通过 `slot_sources` 说明来源，便于 trace 中解释为什么用了这个设备或 artifact。

### 4.3 必须阻止静默继承的情况

以下情况不会悄悄沿用旧上下文：

- 用户明确切换到另一个设备；
- “它/这个故障”对应多个候选设备或故障码；
- 上一轮设备已超出当前身份的 `asset_scope`；
- 当前身份无权读取上一轮报告或运行数据；
- 用户正在询问“我有什么权限”，此时会抑制诊断对象继承；
- 目标 artifact 类型不支持本次续问；
- 证据已 stale，需要刷新或在输出中明确披露。

歧义无法消除时，路由会切换到 `clarification` Skill，由 clarification node 返回具体补充问题，而不是冒险查询错误设备。

### 4.4 时效管理

Artifact 和 evidence 会携带 `freshness`、采样时间和数据窗口。上一轮证据滞后时：

- 状态类问题倾向重新查询运行数据；
- 报告或工单可复用历史分析，但必须标记不代表当前实时状态；
- 工单准备会设置 `stale_refresh_required`；
- 证据账本会自动补充 stale disclosure，防止最终回答把历史证据写成当前事实。

## 5. Skill：能力包如何选择和组合

Skill 位于 [`skills/`](./skills/)。目前包括：

| Skill | 主要用途 | 常见节点 |
| --- | --- | --- |
| `clarification` | 缺少设备、故障码或目标不唯一时追问 | clarification |
| `fault_code_explain` | 查询故障码含义、原因、手册字段 | RAG，可组合 KG |
| `runtime_status` | 查询授权设备当前或最近运行状态 | SQL |
| `alarm_triage` | 综合告警定义与当前状态做初步诊断 | RAG、SQL、analysis |
| `root_cause` | 基于数据和知识证据分析可能根因 | RAG、SQL、analysis |
| `report_generation` | 基于已有或本轮产物生成报告草稿 | report，必要时带分析节点 |
| `workorder_decision` | 判断是否建议工单并生成受限草稿 | workorder、approval |

每个 Skill 包通常包含：

```text
skill.yaml       元数据、槽位、节点、工具、证据、风险、输出和安全合同
schema.yaml      输入结构
examples.yaml    示例
prompt.md        Skill 提示词
validators.py    输入、证据和输出校验器
```

[`skills/registry.py`](./skills/registry.py) 先只发现 `skill.yaml`；[`skills/loader.py`](./skills/loader.py) 再只加载已选 Skill 的 schema、examples、prompt 和 validators，避免把所有领域规则一次性塞入上下文。

Skill validator 还有额外静态限制：只能导入安全的合同模块，不能直接调用授权、SQL、知识库或工具网关。这保证 Skill 校验器不能成为绕过运行时权限的后门。

### 5.1 组合规则

一轮可以选择多个 Skill，但必须有一个 primary Skill。例如：

- “查 J1 当前状态并解释 F3001”会组合 `runtime_status + fault_code_explain + alarm_triage`；
- “诊断后判断是否需要工单”会把诊断 Skill 放在前面，`workorder_decision` 放在最后；
- “生成报告并判断工单”以报告为主，工单仍是后续受限步骤。

组合后并不是多个 Agent 对话，而是多个 Skill 合同共同编译成一个 `ExecutionPlan`。

### 5.2 当前实现边界

Skill 中的 prompt、examples 和 schema 已支持按需加载，但当前生产 `PlanCompiler` 默认依据 Skill 元数据确定性生成计划；只有显式传入 `llm_candidate_plan` 时才采用外部候选计划，而真实 SSE 主链目前没有传它。因此当前系统应理解为“Skill 驱动的受控规划”，不是“LLM 自由规划”。

## 6. 计划如何生成与校验

[`planning/compiler.py`](./planning/compiler.py) 把 Skill 集合编译为 `ExecutionPlan`，主要包含：

- `goals`：每个 Skill 对应的结构化目标；
- `nodes`：typed node 及输入；
- `edges`：节点依赖；
- `required_evidence`：必须收集或披露的证据；
- `allowed_tools` / `forbidden_tools`：候选工具边界；
- `risk_level`：计划最高风险；
- `approval_requirements` / `interrupts`：人工确认边界；
- `expected_outputs` / `output_contract`：允许输出什么、必须有哪些字段、禁止声称什么；
- `fallbacks`：缺数据或权限不足时的降级策略。

候选计划不可信，必须经过 [`planning/validator.py`](./planning/validator.py)。Validator 会检查：

1. Skill 声明是否允许这些节点和工具；
2. 全局禁止工具是否被请求；
3. workflow 能力是否在角色权限内；
4. 设备是否在 `asset_scope`；
5. 数据表是否在 `table_scope`；
6. 每个工具调用是否获得权限；
7. 高风险计划是否声明证据和审批；
8. typed node 输入是否符合合同；
9. 输出是否满足 Skill 的 required fields / forbidden claims；
10. 移除工具后节点是否仍可执行，否则清理或阻断。

校验结果只有三种可执行语义：

- `validated`：正常通过；
- `degraded`：移除部分能力或按策略降级后通过；
- `blocked`：不能安全执行。

Runtime 还会检查 `plan_version` 是否带 `.validated`。未经校验的 candidate plan 不能直接进入真实执行器。

## 7. 权限管理：不是一次 if，而是多层防线

权限实现主要在 `fault_diagnosis/domain/security/`，Agent 侧通过 capability、policy bridge、plan validator 和真实工具节点共同使用。

### 7.1 身份来源

授权依据是服务端构造的 `AuthContext`，包含：

- `role`：`guest`、`engineer`、`admin`；
- `permissions`：workflow、tool、data、KB、admin 能力点；
- `asset_scope`：可访问设备；
- `table_scope`：可访问数据表；
- `system_scope` / `location_scope`；
- `kb_scopes`：public / internal / restricted；
- 用户、session 和认证方式审计信息。

权限由服务端角色策略生成，不能相信前端传入的 permission 列表。

### 7.2 第一层：能力预授权

有效语义形成后，`authorize_capability_preflight()` 先判断角色能否做这类任务：

- 故障码解释需要 knowledge QA 权限；
- 状态查询需要 status query 权限；
- 故障诊断、健康评估、根因分析分别需要对应 workflow 权限；
- 报告生成需要 report generation 权限；
- 工单判断和草稿需要 action request 权限。

这一层发生在澄清和工具规划之前，避免向无权限用户继续暴露敏感诊断对象。游客请求正式报告是一个特例：允许把语义降级成授权设备的运行状态摘要。

### 7.3 第二层：计划级 RBAC + ABAC

`PlanValidator` 同时校验：

- RBAC：这个角色有没有 workflow/tool 权限；
- ABAC：具体设备、表、系统、位置和知识库可见性是否在范围内。

例如工程师有 SQL read 权限，但查询的设备不在其 `asset_scope`，计划仍然会被阻断；有报告权限，但上一轮 artifact 指向越权设备，也不能生成报告。

### 7.4 第三层：工具调用边界

计划允许工具后，真实节点仍通过领域安全模块执行具体约束。典型包括：

- SQL 表白名单、只读检查、资产行级过滤、时间窗口、最大行数；
- RAG 的 KB visibility 过滤；
- 报告写入和访问范围；
- 工单草稿身份、设备范围和证据状态校验。

这防止“计划看起来合法，但节点输入被篡改”后直接触达底层资源。

### 7.5 全局禁止与高风险动作

以下能力在 Agent 计划中全局禁止或强阻断：

```text
sql.write
config.write
device_control.write
workorder.dispatch
alarm.acknowledge
alarm.close
```

当前 Agent 可以给出工单建议，符合权限时可形成**草稿产物**，但不能自动派发、分配、执行或关闭工单，也不能重启、复位、停机或修改设备参数。

`ApprovalNode` 不是一个“用户点了确认就自动执行危险动作”的万能开关。对于工单草稿，它只表达人工确认和 `draft_only`；对于 device/config/dispatch 等危险请求，`allowed_next_step` 会归一化为 `deny`，运行时直接阻断。

## 8. Typed nodes：各节点做什么

真实节点注册在 [`runtime/nodes/__init__.py`](./runtime/nodes/__init__.py)。所有节点实现相同协议：输入是已校验 node + `RuntimeState`，输出是 `NodeExecutionOutput`。

| 节点 | 作用 | 主要产物 |
| --- | --- | --- |
| `clarification` | 根据缺失槽位生成明确追问 | clarification artifact |
| `sql` | 执行受限 SQL，整理行、时间窗和状态证据 | sql artifact、rows、运行数据 evidence |
| `rag` | 检索手册/知识库，解析故障码知识 | knowledge artifact、manual evidence、claims |
| `kg` | 执行知识图谱查询；未配置时明确产生不可用说明 | KG evidence 或 not-configured evidence |
| `analysis` | 合并 SQL 与知识产物，形成诊断分析 | analysis artifact、结构化分析、claims |
| `report` | 使用上一轮或本轮结构化材料保存报告 | report artifact / URL |
| `workorder` | 生成建议、待确认动作和草稿，不派发 | suggestion、pending action、draft artifact |
| `approval` | 形成审批中断或危险动作拒绝边界 | interrupts / blocked result |

节点之间通过 `RuntimeState.artifacts` 传递结构化产物，而不是互相读取自然语言日志。例如 analysis node 读取 SQL 与 knowledge artifact，report node 再读取 analysis artifact 和运行数据构建报告 payload。

## 9. 证据链：结论为什么可信

[`evidence/ledger.py`](./evidence/ledger.py) 管理本轮 `EvidenceLedger`。它与普通日志不同：日志用于观察过程，账本用于约束最终结论。

### 9.1 三层语义

1. **Evidence item**：SQL 采样、手册字段、知识库片段、时间序列特征等原始依据。
2. **Claim**：Agent 想表达的状态、原因、风险或建议，每条 claim 引用 supporting / contradicting evidence id。
3. **Evidence bundle**：账本完成后的兼容投影，供输出、artifact、报告和前端使用。

### 9.2 提交规则

节点不能直接把任意内容当最终证据。Writer 会：

- 标准化 evidence id、来源、节点和摘要；
- 附加当前授权范围；
- 过滤明确未授权的证据；
- 过滤工具报错和空结果伪证据；
- 按来源和内容去重；
- 记录 claim 与 evidence 的引用；
- 在完成阶段检查悬空引用和无证据结论。

只有同时满足以下条件的 claim 才能进入 `final_claim_ids`：

- 状态允许成为最终结论；
- 至少有一条 supporting evidence；
- 所有引用都真实存在；
- 引用的 evidence 没有因越权被过滤。

### 9.3 质量检查

[`evidence/quality.py`](./evidence/quality.py) 至少检查：

- `evidence_count` / `claim_count`；
- 是否存在 final claim；
- final claim 是否都有证据；
- 是否有 dangling evidence reference；
- 是否引用了未授权证据；
- 缺失证据是否已经披露；
- stale evidence 是否刷新或披露。

如果证据不足，系统应输出“不足以确认”及缺失项，而不是把候选原因写成确定事实。证据过时时也必须说明“不代表当前实时状态”。

## 10. 输出、SSE、Artifact 与 Trace 如何配合

### 10.1 OutputFrame

[`output/answer.py`](./output/answer.py) 根据节点结果、artifact 和 evidence bundle 选择回答变体，例如：

- 状态简报；
- 故障码知识解释；
- 诊断/claim 回答；
- 报告结果；
- 工单建议或草稿；
- 权限拒绝、澄清、失败或取消。

它还执行输出合同检查。例如 `status_brief_v2` 必须带结构化 `runtime_status_assessment`，合同未满足就不能伪装成合格状态简报。

### 10.2 SSE 投影

[`output/sse_projection.py`](./output/sse_projection.py) 把内部状态映射为前端事件：

```text
start
  -> task_update
  -> tool_start / tool_end
  -> token
  -> complete
```

`complete` 中既有 V2 核心数据，也可能包含 `workflow_route`、`workflow_policy`、`todos` 等前端兼容字段。兼容字段不代表内部又运行了一套旧 workflow。

### 10.3 Artifact

[`output/artifact_manifest.py`](./output/artifact_manifest.py) 为 SQL、知识、分析、报告和工单产物建立稳定 manifest；[`output/artifact_projection.py`](./output/artifact_projection.py) 再投影为持久化 `DiagnosisArtifactEnvelope`。

Artifact 有两个作用：

1. 保存本轮可审计结果；
2. 成为下一轮上下文的安全载体。

这也是为什么验证“上一轮是否真的被复用”时，应查看 artifact id、manifest、resolved context 和 trace，而不只看最终回答文字。

### 10.4 Trace

规划快照记录 request understanding、context resolution、skill route、candidate plan 和 validation；运行 trace 记录节点状态、输入/输出摘要、耗时、重试、错误、interrupt 和证据质量。

推荐排错顺序：

1. 看 `effective_request_frame`，确认最终语义和槽位来源；
2. 看 `authorization_decision`，确认是否放行、降级或拒绝；
3. 看 `skill_route` 和 validated `execution_plan`；
4. 看每个 `NodeResult.status`、error、tool refs；
5. 看 evidence quality 与 artifact manifest；
6. 最后再看渲染后的 `final_answer`。

## 11. 三个典型请求是如何走的

### 11.1 “查 J1 号机当前状态”

```text
IntentFrame: check_current_status + device=J1号机
ContextFrame: new_case 或显式设备修正
EffectiveRequest: check_runtime_status
能力授权: workflow.status_query
SkillRoute: runtime_status
ExecutionPlan: sql
PlanValidator: 校验设备、表、sql.read
SqlNode: 查询授权时间窗并生成运行数据证据
EvidenceLedger: 状态 claim 引用 SQL evidence
OutputFrame: status_brief_v2
```

### 11.2 “F3001 是什么，J1 现在还有吗？”

```text
IntentFrame: explain_fault_code + check_current_status
EffectiveRequest: check_runtime_status（状态查询是本轮主语义）
SkillRoute: alarm_triage + runtime_status
ExecutionPlan: rag -> sql -> analysis
RagNode: 由 alarm_triage 补充故障码知识与来源
SqlNode: 当前设备状态
AnalysisNode: 综合历史知识和当前数据
EvidenceLedger: 区分手册定义与当前运行事实
OutputFrame: 诊断回答，并披露缺失/过时证据
```

### 11.3 “基于刚才结果生成报告，再看看要不要工单”

```text
ContextFrame: report_handoff / action_followup
EffectiveRequest: 绑定上一轮 reportable artifact 和 evidence bundle
能力预授权: action request；计划校验继续检查组合计划中的报告与工单权限
SkillRoute: report_generation + workorder_decision
ExecutionPlan: analysis -> report -> workorder -> approval
PlanValidator: 重查 artifact 权限、设备范围、时效和审批要求
ReportNode: 生成报告草稿
WorkorderNode: 只形成建议/待确认草稿
Approval boundary: 不允许自动派发或执行
```

## 12. 目录职责速查

```text
agent/
  engine.py                 规划快照总编排，不执行真实工具
  contracts.py              V2 稳定数据合同
  flags.py                  当前引擎模式兼容配置（固定 v2）
  cutover.py                旧导入路径兼容转发

  understanding/
    intent_frame.py         本轮字面意图、实体、风险、歧义
    rewrite.py              用户/RAG/SQL/KG 查询重写

  context/
    resolver_adapter.py     domain context -> V2 ContextFrame
    effective_request.py    当前消息 + 安全继承 -> 唯一有效请求

  skills/
    registry.py             发现 Skill 元数据
    loader.py               只加载命中 Skill 的完整能力包
    router.py               选择、组合 Skill，必要时转 clarification
    */skill.yaml            槽位、证据、节点、工具、风险和安全合同

  planning/
    compiler.py             Skill -> candidate ExecutionPlan
    validator.py            权限、资源、工具、风险、输入和输出校验
    policy_bridge.py        V2 名称与领域安全策略/旧工具名的桥接
    plan_diff.py            计划比较辅助，不是生产主决策链

  runtime/
    plan_preparer.py        补齐真实运行输入并二次校验
    graph.py                构建和校验 DAG
    executor.py             节点调度、重试、取消、证据提交和收尾
    state.py                单次运行状态与结果合同
    tool_runtime.py         对现有 SQL/KB/report 工具的同步适配
    nodes/                  各类真实 typed node

  evidence/
    ledger.py               证据/claim 提交、过滤、去重和固化
    mappers.py              领域 artifact -> evidence
    claims.py               输出 -> claim
    quality.py              引用、授权、缺失和时效质量检查
    projection.py           EvidenceLedger -> EvidenceBundle

  output/
    answer.py               最终回答与输出合同
    report.py               报告 payload
    artifact_manifest.py    可续问产物 manifest
    artifact_projection.py  持久化 envelope 投影
    diagnosis_payload.py    标准诊断合同投影
    sse_projection.py       前端 SSE 和兼容字段

  observability/
    compare.py              计划/结果比较辅助
```

## 13. 修改或新增能力时应该改哪里

### 新增一个诊断 Skill

1. 在 `skills/<name>/` 定义 `skill.yaml`、schema、examples、prompt、validators；
2. 在 router 中增加语义到 Skill 的受控映射；
3. 如需新节点，在 contracts 中定义输入合同，在 runtime/nodes 中实现 typed node；
4. 在 policy bridge 中补齐 policy、task family、goal type、工具名映射；
5. 在 validator 中加入服务端硬约束；
6. 在 evidence mapper/claims 中定义证据和结论关系；
7. 在 output 中增加回答与 artifact 投影；
8. 补齐 plan、权限、runtime、证据和 SSE 测试。

不要只在 prompt 里写“禁止越权”或“必须有人确认”。安全边界必须落在服务端 Validator、工具网关和真实节点中。

### 新增一个工具

至少需要同时回答：

- 哪些 Skill 可以声明它？
- 对应哪个 permission？
- 是否全局禁止或需要 approval？
- 能访问哪些 asset/table/KB scope？
- 输入怎样做确定性校验？
- 成功、空结果、失败分别产生什么 evidence？
- 输出中哪些 claim 可以引用它？
- trace 和审计记录什么？

如果这些问题没有答案，不应把工具直接接进 `ToolRuntime`。

## 14. 当前边界与阅读时的注意事项

- 当前是单 Agent + 多 Skill + typed DAG，不是多 Agent。
- 当前生产意图、路由和默认计划主要是规则驱动；LLM 候选计划是保留接口，不是主链事实。
- Skill prompt/examples 已按需加载，但尚不能据此推断生产中一定调用了 LLM。
- KG node 存在于运行时合同中，但具体可用性要看环境和节点实现，未配置时必须明确降级。
- `flags.py` 当前固定返回 V2；`cutover.py` 只是兼容旧 import，不表示仍有双引擎切流。
- 前端显示的历史消息与 Agent 实际复用的结构化上下文是两件事。
- `workflow_*`、旧 task/intent 字段是输出兼容投影，不是 V2 内部的第二套决策系统。
- 报告和工单必须基于可引用 artifact；报告缺少可报告材料会失败，工单永远不能由 Agent 自动派发。
- 任何最终诊断可信度都应以 evidence/claim 引用和 quality checks 为准，不能只看回答措辞或最后一行日志。

## 15. 建议的验证清单

修改 Agent 后，至少验证以下行为：

- 明确设备的新请求不会继承上一设备；
- 多候选指代会进入 clarification；
- 越权 artifact、设备、表和 KB 文档不会被复用；
- guest 报告请求只会被拒绝或按策略降级，不生成正式报告；
- candidate plan 不能绕过 validator 直接执行；
- 全局禁止工具不会进入 Runtime；
- 工单结果包含 `draft_only=true`、`manual_confirmation_required=true`、`dispatch_forbidden=true`；
- final claim 都引用存在且已授权的 evidence；
- 缺失和 stale evidence 在最终输出中有披露；
- SSE `complete` 中能追溯 effective request、validated plan、node result、evidence bundle 和 artifact；
- 停止请求能让未执行节点取消，而不是继续调用真实工具。

仓库常用验证命令：

```bash
PYTHONPATH=. pytest -q
python -m compileall fault_diagnosis/agent
git diff --check
```

如果只想快速理解一次真实执行，优先从 `server/agent_gateway/streaming.py::token_stream_events` 开始，依次跟进 `engine.py`、`runtime/plan_preparer.py`、`runtime/executor.py`，再查看 `evidence/` 和 `output/`。这条阅读路径与生产请求的真实顺序一致。
