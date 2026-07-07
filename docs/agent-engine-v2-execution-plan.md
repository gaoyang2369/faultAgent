# Agent Engine V2 架构迁移执行计划

本文是 faultAgent 从当前限制型单 Agent 主链路迁移到 `Agent Engine V2` 的目标态执行计划。它是规划文档，不代表当前生产链路已经具备这些能力。

迁移采用同仓库旁路重建方式：保留现有 API、Session、Auth、SSE、artifact、报告和工单合同，在新目录中逐步实现 V2 引擎，通过 feature flag、plan-only、shadow compare 和分 skill 切流替换旧 `single_agent/flow.py` 主链路。

## 目标

最终目标是把当前“硬编码固定大流水线”调整为“LLM 理解和规划 + Skill 渐进加载 + 服务端策略校验 + 有界 typed workflow + 证据账本 + 兼容输出”的结构。

目标态链路：

```text
API / Session / Auth / SSE
        |
Request Understanding Layer
- LLM 意图拆解
- 用户语句重写
- 设备/故障码/时间窗口抽取
- 歧义识别
        |
Context + Skill Router
- 上下文复用
- 选择 fault_code_explain / runtime_status / alarm_triage / root_cause / report / workorder 等 skill
- 只加载命中的 skill prompt、schema、examples
        |
Plan Compiler
- LLM 生成候选 ExecutionPlan
- 系统根据权限、risk、evidence requirement 修正/拒绝
        |
Workflow Runtime
- 执行 typed nodes
- SQL/RAG/KG/report/workorder 都是 node
- 支持 interrupt、approval、retry、trace
        |
Evidence Ledger
- SQL evidence
- manual/KG evidence
- time-series evidence
- claim/evidence 引用校验
        |
Output Layer
- 状态简报
- 诊断报告
- 工单草稿
- 前端 SSE projection
```

## 非目标

- 不推倒重写前端、API、认证、权限、报告访问和工单接口。
- 不把系统改成开放式 ReAct 或让 LLM 自由调用工具。
- 不让 LLM 决定最终权限、SQL 执行范围、工单派发或设备动作。
- 不把旧 `workflow_*`、旧任务类型和旧意图字段重新变成内部决策来源。
- 不一次性删除现有 `single_agent/`，旧链路先作为 baseline 和回滚路径保留。

## 架构原则

1. LLM proposes, system disposes  
   LLM 可以生成 `IntentFrame`、`RewriteFrame` 和候选 `ExecutionPlan`，但权限、风险、工具白名单、证据要求和动作边界由服务端确定性校验。

2. Skill 渐进加载  
   每轮只加载命中的 skill prompt、schema、examples 和 validator，避免把所有诊断知识、报告规则、工单规则塞进一个大 prompt 或大函数。

3. Evidence first  
   诊断结论、报告摘要、工单建议必须引用 `EvidenceItem` / `Claim` / `EvidenceBundle` 或 V2 `EvidenceLedger`，不能只存在日志里。

4. 有界 workflow  
   Runtime 执行 typed nodes，节点由经过校验的 `ExecutionPlan` 驱动。SQL、RAG、KG、报告、工单都是节点，不是模型自由工具调用。

5. 输出兼容，内部重构  
   V2 内部使用新合同，输出层继续投影现有 SSE `complete` payload、artifact 和前端字段，直到前端可独立升级。

6. 先 plan，再执行  
   所有新能力先支持 plan-only 和 shadow compare，再接入真实工具执行。

7. 高风险动作人工确认  
   工单和设备动作只能生成建议、草稿或人工确认要求。不能自动派发工单，不能自动重启、复位、停机或修改参数。

## 目标目录

建议新增目录，不直接在旧 `single_agent/flow.py` 上继续堆功能：

```text
fault_diagnosis/agent_engine/
  __init__.py
  contracts.py
  engine.py
  flags.py
  understanding/
    __init__.py
    intent_frame.py
    rewrite.py
    extractors.py
    prompts.py
  context/
    __init__.py
    resolver_adapter.py
    context_frame.py
  skills/
    __init__.py
    registry.py
    loader.py
    base.py
    fault_code_explain/
      skill.yaml
      prompt.md
      schema.yaml
      examples.yaml
      validators.py
    runtime_status/
    alarm_triage/
    root_cause/
    report_generation/
    workorder_decision/
  planning/
    __init__.py
    compiler.py
    validator.py
    policy_bridge.py
    plan_diff.py
  runtime/
    __init__.py
    graph.py
    executor.py
    state.py
    nodes/
      sql.py
      rag.py
      kg.py
      analysis.py
      report.py
      workorder.py
      approval.py
  evidence/
    __init__.py
    ledger.py
    mappers.py
    quality.py
  output/
    __init__.py
    answer.py
    report.py
    sse_projection.py
    artifact_projection.py
  observability/
    __init__.py
    trace.py
    eval_events.py
```

旧目录保留：

```text
fault_diagnosis/single_agent/
```

旧链路职责在迁移期是 baseline、兼容参考和回滚路径。迁移完成后再制定单独退役计划。

## 核心合同

V2 先定义合同，再实现逻辑。合同需要尽量小而稳定。

### IntentFrame

表示用户本轮请求的结构化理解。

必须包含：

- `raw_message`
- `normalized_message`
- `language`
- `intent_candidates`
- `primary_intent`
- `sub_intents`
- `entities`
- `device_refs`
- `fault_code_refs`
- `time_window`
- `requested_outputs`
- `risk_hints`
- `ambiguities`
- `confidence`
- `model_trace`

要求：

- 不能包含授权结论。
- 不能直接决定工具调用。
- 必须保留原始用户问题和模型理解依据摘要。

### RewriteFrame

表示对用户问题和检索问题的重写。

必须包含：

- `user_rewrite`
- `rewrite_reason`
- `retrieval_queries`
- `sql_question`
- `manual_query`
- `kg_query`
- `staleness_note`

要求：

- 重写结果不能覆盖原始问题。
- 重写必须进入 trace。
- 续问重写必须显式说明引用了哪个 thread artifact 或 active case。

### ContextFrame

表示多轮上下文解析结果。

必须包含：

- `relation_to_previous`
- `active_case_id`
- `referenced_artifact_id`
- `inherited_slots`
- `stale_evidence`
- `missing_context`
- `permission_context`
- `reuse_decision`
- `reuse_blockers`

要求：

- 复用上下文必须满足 thread、权限、设备、时间、artifact 类型和 staleness 条件。
- 越权、设备切换、歧义和 stale evidence 不能静默继承。

### SkillRoute

表示本轮命中的 skill。

必须包含：

- `selected_skills`
- `primary_skill`
- `skill_inputs`
- `load_set`
- `skill_confidence`
- `routing_reason`
- `blocked_skills`

要求：

- 同一轮可以命中多个 skill，但必须有一个 primary skill。
- skill loader 只加载命中的 prompt、schema、examples 和 validator。
- skill 只能声明能力和证据需求，不能绕过服务端权限。

### ExecutionPlan

表示可执行计划。

必须包含：

- `plan_id`
- `plan_version`
- `goals`
- `nodes`
- `edges`
- `required_evidence`
- `allowed_tools`
- `forbidden_tools`
- `risk_level`
- `interrupts`
- `approval_requirements`
- `expected_outputs`
- `fallbacks`

要求：

- LLM 生成的是 candidate plan。
- `PlanValidator` 输出 validated plan。
- Runtime 只能执行 validated plan。
- validated plan 必须写入 trace 和 complete payload 摘要。

### NodeResult

表示节点执行结果。

必须包含：

- `node_id`
- `node_type`
- `status`
- `input_summary`
- `output`
- `evidence_refs`
- `tool_call_refs`
- `error`
- `retry_count`
- `duration_ms`

要求：

- 节点失败要区分 `blocked`、`skipped`、`failed`、`cancelled`。
- 节点结果必须能映射到 evidence 或明确说明没有 evidence。

### EvidenceLedger

V2 可以复用现有 `EvidenceItem` / `Claim` / `EvidenceBundle` 语义，但内部命名建议使用 `EvidenceLedger`，最后再投影成现有 `EvidenceBundle`。

必须包含：

- `ledger_id`
- `task`
- `evidence_items`
- `claims`
- `final_claim_ids`
- `quality_checks`
- `artifact_refs`
- `authorization_refs`

质量检查至少包括：

- `evidence_count`
- `claim_count`
- `all_claims_have_evidence`
- `no_dangling_evidence_refs`
- `missing_evidence_disclosed`
- `has_current_status`
- `has_alarm_history`
- `has_manual_reference`
- `has_timeseries_feature`
- `no_unauthorized_evidence_refs`

### OutputFrame

表示最终可输出内容。

必须包含：

- `answer_variant`
- `final_answer`
- `status_brief`
- `diagnosis_report_payload`
- `workorder_draft_payload`
- `sse_payload`
- `artifact_payload`
- `guardrail_result`

要求：

- 输出层只做表达和兼容投影，不重新做诊断。
- 状态类默认简短，诊断类按证据展开，报告类交给报告 renderer。

## Skill 包规范

每个 skill 是一个可版本化的小包：

```text
skills/{skill_name}/
  skill.yaml
  prompt.md
  schema.yaml
  examples.yaml
  validators.py
```

`skill.yaml` 必须包含：

- `name`
- `version`
- `description`
- `trigger_intents`
- `required_slots`
- `optional_slots`
- `required_evidence`
- `allowed_nodes`
- `allowed_tools`
- `forbidden_tools`
- `risk_level`
- `output_variants`
- `fallback_policy`

首批 skill：

| Skill | 用途 | 默认节点 | 风险 |
| --- | --- | --- | --- |
| `fault_code_explain` | 解释故障码、告警码、手册含义 | RAG/KG | 低 |
| `runtime_status` | 查询当前或最近运行状态 | SQL/time-series | 中 |
| `alarm_triage` | 故障码解释 + 当前状态 + 初步处置 | SQL + RAG/KG + analysis | 中 |
| `root_cause` | 根因/严重性/影响分析 | SQL + RAG/KG + analysis + evidence validation | 高 |
| `report_generation` | 基于 artifact 或本轮证据生成报告 | report | 中 |
| `workorder_decision` | 判断是否建议工单草稿 | analysis + workorder + approval boundary | 高 |

## 执行阶段

### Phase 0: Baseline 与保护网

目标：

- 冻结当前主链路行为，建立 V2 迁移前的可比较基线。

工作项：

- 记录当前 `/chat/stream`、`/chat/plan`、`/agent/chat`、报告、工单、历史、权限的核心契约。
- 整理现有测试矩阵，标出哪些是 V2 必须继承的行为。
- 新增 `AGENT_ENGINE_VERSION=legacy|v2_plan|v2_shadow|v2` 配置草案。
- 新增 V2 迁移检查清单，不改生产路径。

产物：

- baseline case list。
- V2 feature flag 设计。
- 当前契约继承清单。

Phase 0 的冻结清单、测试矩阵和 feature flag 设计记录在
[Agent Engine V2 Phase 0 Baseline](./agent-engine-v2-phase0-baseline.md)。

验收：

- `PYTHONPATH=. pytest -q` 通过。
- 没有任何线上路径默认切到 V2。
- 能明确列出至少 20 条端到端迁移用例。

### Phase 1: V2 合同与空引擎

目标：

- 建立 `agent_engine/` 目录和核心合同，但不执行真实工具。

工作项：

- 新增 `agent_engine/contracts.py`。
- 定义 `IntentFrame`、`RewriteFrame`、`ContextFrame`、`SkillRoute`、`ExecutionPlan`、`NodeResult`、`EvidenceLedger`、`OutputFrame`。
- 新增 `AgentEngineV2.plan_only()` 空实现。
- 新增 `PlanSnapshotV2` 输出，用于 `/chat/plan` 或内部测试。

产物：

- V2 合同。
- plan-only 空引擎。
- 单元测试覆盖合同序列化、默认值、schema 稳定性。

验收：

- 不触碰旧 `single_agent/flow.py` 业务逻辑。
- V2 合同能 JSON 序列化。
- V2 plan-only 能返回空计划和明确的 `not_implemented` 状态。

Phase 1 产物应保持为旁路合同与空引擎，尚未接入 `/chat/plan`、`/chat/stream` 或 `/agent/chat`。

### Phase 2: Request Understanding Layer

目标：

- 引入 LLM 意图拆解、用户语句重写、实体抽取和歧义识别。

工作项：

- 实现 `understanding/intent_frame.py`。
- 实现 `understanding/rewrite.py`。
- 抽取设备、故障码、时间窗口、输出意图、动作风险。
- 支持规则 fallback。
- 将原始问题、重写问题、重写理由写入 trace。

产物：

- `IntentFrameBuilder`。
- `RewriteFrameBuilder`。
- intent/rewrite 单元测试。

验收：

- “A07089 是什么，现在 J1 还故障吗，要不要工单”能拆成多个 sub intent。
- “基于刚才结果生成报告”能重写并保留 artifact 引用意图。
- “那 J2 呢”能识别显式设备切换。
- 模型失败时 fallback 可用。

### Phase 3: Context + Skill Router

目标：

- 复用现有上下文能力，并引入 skill 渐进加载。

工作项：

- 新增 `context/resolver_adapter.py`，适配现有 `ContextResolver` 和 `CaseState`。
- 新增 `skills/registry.py`、`skills/loader.py`。
- 实现首批 skill 的 `skill.yaml`、`schema.yaml` 和基础 examples。
- 实现 `SkillRouter`，输入 `IntentFrame`、`RewriteFrame`、`ContextFrame`，输出 `SkillRoute`。

产物：

- `ContextFrame` 适配器。
- skill registry。
- skill route plan-only 输出。

验收：

- 每轮只加载命中的 skill。
- 上下文歧义时选中 `clarification` 或阻塞相关 skill。
- 权限范围问题不复用上一轮诊断上下文。
- 旧 context 测试对应行为不倒退。

### Phase 4: Plan Compiler 与 Policy Validator

目标：

- 让 LLM 生成候选计划，由系统裁决成 validated plan。

工作项：

- 实现 `planning/compiler.py`，根据 skill 输入生成 candidate `ExecutionPlan`。
- 实现 `planning/validator.py`，校验权限、risk、evidence、allowed tools、forbidden tools。
- 实现 `planning/policy_bridge.py`，桥接现有 `workflow/policies.py` 和 `security/policy_engine.py`。
- 实现 `planning/plan_diff.py`，比较 legacy plan 与 V2 plan。

产物：

- candidate plan。
- validated plan。
- plan diff。

验收：

- 访客不能生成报告、根因诊断和工单草稿。
- 工程师只能访问授权设备和表。
- workorder 和 device action 都生成 approval requirement。
- LLM 候选计划要求危险工具时会被 validator 拒绝或降级。

### Phase 5: Workflow Runtime

目标：

- 用 typed nodes 替换大函数式阶段编排。

工作项：

- 实现 `runtime/state.py`、`runtime/executor.py`、`runtime/graph.py`。
- 实现 node 生命周期：`pending`、`running`、`completed`、`skipped`、`blocked`、`failed`、`cancelled`。
- 实现 retry、interrupt、approval boundary、cancel handle、trace event。
- 首批 node 只接 plan-only fake executor，然后逐步接真实工具。

产物：

- V2 runtime。
- typed node 基类。
- fake node tests。

验收：

- Runtime 只能执行 validated plan。
- cancel 能停止执行并返回兼容 complete/cancel payload。
- node 失败不会污染 evidence ledger。
- trace 能还原节点顺序、输入摘要、输出摘要、耗时和错误。

### Phase 6: 工具节点迁移

目标：

- 将 SQL、RAG/KG、分析、报告、工单拆成独立 node。

工作项：

- `nodes/sql.py` 复用现有 SQL safety、ACL、query tool、parser。
- `nodes/rag.py` 复用现有知识库工具和 RAG ACL。
- `nodes/kg.py` 预留知识图谱接口，初期可返回 `skipped/not_configured`。
- `nodes/analysis.py` 复用现有诊断分析能力并收敛输出 schema。
- `nodes/report.py` 复用现有 report payload 和 `save_report`。
- `nodes/workorder.py` 复用现有工单建议和草稿边界。
- `nodes/approval.py` 统一人工确认要求。

产物：

- 真实工具 node。
- node result 到 evidence mapper。
- 每个 node 的权限和失败测试。

验收：

- SQL 仍只读、白名单、ACL 重写、设备范围限制。
- 知识库结果仍经过可见性过滤。
- 报告仍写私有报告目录并通过 `/reports/{filename}` 读取。
- 工单仍只生成建议或草稿，不派发。

### Phase 7: Evidence Ledger

目标：

- 把证据链从旧阶段产物中抽离为 V2 一等公民。

工作项：

- 实现 `evidence/ledger.py`。
- 实现 SQL/RAG/KG/time-series/manual evidence mapper。
- 实现 claim builder。
- 实现 quality checks。
- 实现 V2 ledger 到现有 `EvidenceBundle` 的投影。

产物：

- `EvidenceLedger`。
- evidence mappers。
- quality validator。

验收：

- 所有 final claim 必须引用 evidence。
- 不存在 dangling evidence refs。
- 缺失证据必须披露。
- stale evidence 必须刷新或披露。
- 未授权 evidence 不能进入 final output。

### Phase 8: Output Layer 与 SSE Projection

目标：

- V2 内部输出和现有前端/SSE/artifact 兼容解耦。

工作项：

- 实现 `output/answer.py`，输出 `status_brief`、`diagnosis_answer`、`clarification` 等 variant。
- 实现 `output/report.py`，报告只消费结构化 payload。
- 实现 `output/sse_projection.py`，投影 `start`、`task_update`、`tool_start`、`tool_end`、`token`、`complete`。
- 实现 `output/artifact_projection.py`，保存现有 `DiagnosisArtifactEnvelope`。
- 保留旧 `workflow_*` 字段为兼容投影。

产物：

- V2 output frame。
- SSE adapter。
- artifact adapter。

验收：

- 前端无需修改即可展示 V2 回答。
- `complete` 中仍包含现有关键字段：decision、sql_artifact、knowledge_artifact、analysis_artifact、report_artifact、evidence_bundle、artifact、todos。
- 旧字段只从新结构单向投影。

### Phase 9: Shadow Compare 与分 Skill 切流

目标：

- 在真实请求中并行生成 V2 plan，对比旧链路，再逐步切换执行。

工作项：

- `AGENT_ENGINE_VERSION=v2_shadow` 时旧链路执行，V2 只生成 plan 和 diff。
- 记录 plan diff：intent、skill、nodes、tools、evidence requirements、risk、approval。
- 先切 `fault_code_explain`。
- 再切 `runtime_status`。
- 再切 `report_generation`。
- 最后切 `alarm_triage`、`root_cause`、`workorder_decision`。

产物：

- shadow diff 日志。
- skill 级 feature flags。
- 迁移 dashboard 或本地 eval summary。

验收：

- 每个 skill 切换前至少通过对应测试和 eval cases。
- 影子对比中高风险分歧必须人工 review。
- 出现异常可以按 skill 回滚到 legacy。

### Phase 10: Legacy 退役

目标：

- 默认使用 V2，旧链路只保留短期回滚，然后删除。

工作项：

- 默认 `AGENT_ENGINE_VERSION=v2`。
- 保留 legacy flag 一个迭代周期。
- 删除不再使用的 legacy route、compat fallback 和大函数分支。
- 更新 `current-architecture.md`、`fault_diagnosis/README.md` 和 `single_agent/README.md`。

产物：

- V2 当前态文档。
- legacy 删除 PR。
- 新架构验收报告。

验收：

- 旧链路删除后全量测试通过。
- 文档不再把旧 `single_agent/flow.py` 作为主链路。
- 没有 shadow/diff/gate 或旧任务类型作为内部决策输入。

## 验收矩阵

### 功能类

- 故障码解释。
- 当前状态查询。
- 告警分诊。
- 根因分析。
- 报告生成。
- 工单建议。
- 工单草稿确认。
- 权限范围问答。
- 多轮续问。
- 设备切换。
- 编辑重生成。
- 停止流。
- 语音 JSON 入口。

### 安全类

- 访客降级。
- 工程师设备越权。
- SQL 表越权。
- RAG 文档越权。
- 报告读取越权。
- 工单状态越权。
- 设备动作拒绝。
- 派发工单拒绝或外部审批提示。

### 证据类

- claim 全部有 evidence。
- 无 dangling evidence refs。
- 缺失证据披露。
- stale evidence 刷新或披露。
- 当前状态来自 SQL/time-series，不来自手册。
- 手册证据不能伪装成实时状态。

### 兼容类

- `/chat/stream` SSE 可被现有前端消费。
- `/chat/stream/edit` 行为不倒退。
- `/agent/chat` 聚合 JSON 不倒退。
- `/reports/{filename}` 访问控制不倒退。
- history 和 artifact 续问不倒退。

## 回滚策略

每个阶段都必须可回滚：

- 全局回滚：`AGENT_ENGINE_VERSION=legacy`。
- skill 回滚：关闭对应 skill 的 V2 execution flag。
- node 回滚：validator 禁用对应 V2 node，回退 legacy stage 或返回 blocked。
- 输出回滚：V2 内部结果继续投影旧 complete payload。

任何阶段只要出现以下问题，停止切流：

- 越权证据进入输出。
- 工单或设备动作被自动执行。
- SQL ACL 被绕过。
- final claim 无 evidence。
- stale evidence 未刷新也未披露。
- 前端无法消费 complete payload。

## Codex 执行方式

后续让 Codex 执行时，建议每次只给一个阶段或一个阶段中的子任务。

推荐格式：

```text
按照 docs/agent-engine-v2-execution-plan.md 的 Phase N 执行：
目标：...
范围：只改 ...
要求：不改 ...
验收：运行 ...
```

每个实施任务都应输出：

- 改了哪些文件。
- 是否触碰生产路径。
- 新增或更新了哪些测试。
- 实际运行了哪些验证命令。
- 还剩哪些未迁移边界。

## 首批建议任务顺序

1. Phase 0: baseline 与 feature flag 草案。
2. Phase 1: V2 合同和空 plan-only engine。
3. Phase 2: IntentFrame 和 RewriteFrame。
4. Phase 3: skill registry 和 `fault_code_explain` / `runtime_status` 两个 skill。
5. Phase 4: PlanCompiler + PlanValidator。
6. Phase 5: fake runtime。
7. Phase 6: SQL/RAG node。
8. Phase 7: EvidenceLedger。
9. Phase 8: SSE/artifact projection。
10. Phase 9: shadow compare。

这个顺序的核心是先建立合同和可观测比较，再接真实工具，最后切流。
