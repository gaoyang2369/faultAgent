# 意图 / 上下文 / Planner 重构最终总结

本文档汇总 Phase 1 到 Phase 4.4.1 的整体改造结果，用于替代分散的阶段审计说明，并记录当前真实执行边界。

## 总体结论

本轮重构是在现有受限单 Agent 工作流外侧增加结构化观测层：

- Phase 1：新增 `ResolvedContext`，用于安全复用基于 artifact 的上下文。
- Phase 2：新增 `GoalSet`，用于表达结构化用户目标和目标依赖。
- Phase 3：新增 `TaskFamily`，用于粗粒度任务分类和后续迁移评估。
- Phase 4.1：新增确定性 `ShadowPlanner`，只生成观测用途的 planner 输出。
- Phase 4.2：新增确定性 `PlanningDiff`，比较 legacy policy 与 shadow planner。
- Phase 4.3：新增 `PlannerGate`，用于低风险只读任务的 planner-gated execution preview。
- Phase 4.4.1：新增 `DiagnosisReadiness`，允许 diagnosis 进入 planner-gate dry-run 观测，但不允许 active 接管。

旧执行主干仍然保留：

```text
TaskType + intent_stack + route flags + WorkflowPolicy + authorization
-> enabled_nodes + runtime_tools
-> restricted single-agent stages and tool calls
```

`ResolvedContext`、`GoalSet`、`TaskFamily`、`ShadowPlanner`、`PlanningDiff`、`DiagnosisReadiness` 都是增量的观测、调试和 eval 层。只有 `PlannerGate` 允许影响执行，而且必须显式开启并处于 active 模式；即使 active，也只能对 `knowledge_lookup`、`runtime_status`、`reporting` 三类只读任务收窄节点和工具资格。Phase 4.4.1 中 diagnosis 只进入 dry-run readiness 观测，真实执行仍保持 `legacy_policy`。

## 当前默认行为

默认运行行为仍然是 legacy policy execution。

- `ENABLE_PLANNER_GATED_EXECUTION=false`
- `PLANNER_GATED_DRY_RUN=true`
- `PLANNER_GATE_DIAGNOSIS_DRY_RUN=true`
- `PLANNER_GATE_ENABLE_DIAGNOSIS_ACTIVE=false`
- `PLANNER_GATED_TASK_FAMILIES=knowledge_lookup,runtime_status,reporting`
- `PLANNER_GATED_REQUIRE_DIFF_STATUS=aligned,acceptable_diff`
- `PLANNER_GATED_MAX_DIFF_SEVERITY=warning`

默认配置下，`planner_gate.selected_execution_source` 始终是 `legacy_policy`。diagnosis 可以生成 dry-run `diagnosis_readiness`，但 `enabled_nodes` 和 `runtime_tools` 仍由 legacy workflow policy 与 authorization 选择。

## 环境变量开关

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `ENABLE_PLAN_ENDPOINT` | `false` | 在本地 / dev eval 路径中启用 `/chat/plan`。 |
| `ENABLE_PLANNER_GATED_EXECUTION` | `false` | 启用 planner gate 评估。disabled 模式永远不改变执行。 |
| `PLANNER_GATED_DRY_RUN` | `true` | gate 开启时只计算 eligibility，仍保持 `legacy_policy`。 |
| `PLANNER_GATE_DIAGNOSIS_DRY_RUN` | `true` | 允许 diagnosis 进入 planner-gate dry-run readiness 观测。 |
| `PLANNER_GATE_ENABLE_DIAGNOSIS_ACTIVE` | `false` | 预留诊断 active 开关；Phase 4.4.1 下不授权 active。 |
| `PLANNER_GATED_TASK_FAMILIES` | `knowledge_lookup,runtime_status,reporting` | 允许进入只读 gate 评估的任务族。 |
| `PLANNER_GATED_REQUIRE_DIFF_STATUS` | `aligned,acceptable_diff` | active projection 允许的 planning diff 状态。 |
| `PLANNER_GATED_MAX_DIFF_SEVERITY` | `warning` | gate 允许的最高 planning diff severity。 |

Phase 4.4.1 没有任何环境变量允许 action/workorder 迁移执行。diagnosis 只允许 dry-run 观测，`ready_for_active` 始终为 `false`。

## 阶段总览

| 阶段 | 目标 | 核心模块 | 主要输出字段 | 是否影响执行 | 验证结果 |
| --- | --- | --- | --- | --- | --- |
| 1 | 从 thread artifacts 中安全解析上一轮上下文。 | `single_agent/context/*`、artifact-backed case projection。 | `resolved_context`、`context_resolution`、`relation_to_previous`、`inherited_slots`、`stale_evidence`、`missing_context`。 | 不影响。供 routing/debug 使用，不替代 `TaskType` 或 policy。 | `scripts/context_acceptance_test.py`：6 passed。 |
| 2 | 用结构化 goals 表达用户意图和依赖。 | `workflow/contracts.py` 中的 `IntentGoal`/`GoalSet`、goal builder/projection。 | `goal_set`、`goals`、`goal_summary`、`intent_stack_projection`、`blocked_goals`。 | 不直接影响工具或节点。projection 会合并进旧 `intent_stack`。 | `scripts/goal_acceptance_test.py`：5 passed。 |
| 3 | 在旧 task type 上增加粗粒度迁移标签。 | `workflow/task_family.py`、`TaskFamilyResolution`。 | `task_family`、`task_family_reason`、`task_family_source`、`task_family_warnings`。 | 不影响。policy、stages、evidence gap、tools 不读取它。 | 已由 `pytest` invariants 和 plan eval 覆盖。 |
| 4.1 | 构建确定性 shadow planner。 | `planning/contracts.py`、`planning/shadow_planner.py`、`planning/summaries.py`。 | compact `shadow_plan`、`shadow_plan_summary`；full plan 只用于离线 artifact/debug。 | 不影响。shadow plan 不被 execution policy/stages/tools 读取。 | `scripts/shadow_planner_acceptance_test.py`：5 passed。 |
| 4.2 | 比较 legacy policy 与 shadow plan。 | `planning/diff_contracts.py`、`planning/diff_evaluator.py`、`planning/diff_summaries.py`。 | compact `planning_diff`、`planning_diff_summary`；full diff 只用于 artifact/debug。 | 不影响。diff 不写 execution flags 或 policy-readable 字段。 | `scripts/planning_diff_acceptance_test.py`：7 passed。 |
| 4.3 | 预览只读 planner-gated execution。 | `planning/gate_contracts.py`、`planning/gate.py`。 | compact `planner_gate`、`planner_gate_summary`、`selected_execution_source`。 | 默认不影响。active 模式只能在 legacy policy/auth/diff 检查之后收窄只读节点和工具。 | `scripts/planner_gate_acceptance_test.py`：11 passed；已生成 observation report。 |
| 4.4.1 | 允许 diagnosis dry-run readiness 观测。 | `planning/diagnosis_readiness.py`、`planning/gate.py`。 | compact `diagnosis_readiness`、`planner_gate.diagnosis_readiness`。 | 不影响。diagnosis 始终 `legacy_policy`，不改变 nodes/tools/stages。 | `scripts/diagnosis_dry_run_acceptance_test.py`：8 passed。 |

## 各层关系

### ResolvedContext

`ResolvedContext` 回答的问题是：当前 turn 可以安全复用哪些历史 artifact 上下文？

它负责 artifact-backed context binding、上下文关系分类、继承槽位、引用 artifact/report id、stale evidence 检测、missing context，以及权限范围内的上下文继承。

它不负责：

- 替代 `TaskType`
- 选择 workflow nodes
- 选择 runtime tools
- 绕过 authorization

它以 compact debug context 的形式出现在 `/chat/plan`、SSE complete payload、`workflow_route`、`decision.resolved_context` 和 trace metadata 中。

### GoalSet

`GoalSet` 回答的问题是：用户当前 turn 想完成什么目标？

它把请求拆解为多个 `IntentGoal`，每个 goal 可以包含依赖、缺失槽位、所需证据、预期输出、风险级别、上下文引用和 blocked 状态。

它与旧执行字段的兼容桥接方式是：

```text
intent_stack = stable_dedupe(goal_set.intent_stack_projection + legacy_intent_candidates)
```

Goals 不直接启用工具。Workorder 相关 goal 只表示草稿、判断或确认建议，绝不表示已经派发工单或完成动作。

### TaskFamily

`TaskFamily` 是主要由旧 `TaskType` 派生出来的粗粒度标签：

- `knowledge_lookup`
- `runtime_status`
- `diagnosis`
- `reporting`
- `action_or_workorder`
- `meta`

它用于 debug、eval、观测和迁移规划。Phase 4.3 active gate 之前，它完全不影响执行。Phase 4.3 中，它只作为 `PlannerGate` 的 eligibility 输入；legacy workflow policy 仍然不消费它。

### ShadowPlanner

`ShadowPlanner` 消费 request summary、auth summary、`ResolvedContext`、`GoalSet`、`TaskType`、`intent_stack`、`TaskFamily` 和 compact evidence refs。

它输出：

- `PlanningDecision`
- `NodePlan`
- `EvidencePlan`
- `ToolPlan`
- `OutputPlan`
- `legacy_projection`
- compact `shadow_plan`

它是确定性的，不调用 LLM，也不选择真实执行工具。`authorized_runtime_tools` 的含义是“在 shadow plan 中被授权，并且已经存在于 legacy runtime tools 中”，不是“新授予执行权限”。

### PlanningDiff

`PlanningDiff` 比较 legacy policy/decision 输出与 shadow plan：

- nodes
- tools
- evidence requirements
- output boundaries
- safety guardrails

状态规则：

- `aligned`：没有有意义的 diff。
- `acceptable_diff`：只有 info，或白名单允许的 non-safety warning。
- `needs_review`：存在 warning/error，但没有 critical。
- `unsafe_mismatch`：存在任何 critical。

`PlanningDiff` 不能影响执行。它不得写入 `decision.flags`、route flags、workflow policy metadata、evidence-gap 输入、node resolver 输入，或 runtime tool invocation 路径。

### PlannerGate

`PlannerGate` 是第一个允许影响执行的新增层，但必须显式 opt-in：

```text
ENABLE_PLANNER_GATED_EXECUTION=true
PLANNER_GATED_DRY_RUN=false
```

active 模式只有在所有 blocker 都不存在时，才允许选择 `planner_gated`：

- 属于允许的只读 task family
- 没有 diagnosis/action/workorder goal
- 没有 ambiguous 或 action-followup context
- planning diff status 和 severity 符合配置
- 没有 critical diff
- shadow authorized tools 是 legacy runtime tools 的子集
- final tools 是安全交集
- safety nodes 被保留
- 没有 missing/denied auth
- 没有 unauthorized inheritance
- 没有 stale workorder migration

最终 runtime tools 为：

```text
shadow authorized tools
INTERSECT legacy runtime_tools
INTERSECT hard allowed tools
INTERSECT authorization-filtered runtime_tools
```

active node projection 仅限：

- `knowledge_lookup` -> `knowledge`
- `runtime_status` -> `sql`
- `reporting` -> `report`

如果 legacy 与 shadow 都保留了 `permission_check`、`risk_check`、`audit_log`、`output_guardrail`、`evidence_validation` 等 safety nodes，active projection 会继续保留它们。如果 shadow 移除了 legacy safety node，gate 会 fallback 到 legacy。

Phase 4.4.1 中，diagnosis 可以进入 dry-run observation，但 gate 会保留 `diagnosis_dry_run_only` 和 `diagnosis_active_not_enabled` blockers。即使全局 gate 处于 active 配置，diagnosis 的 `selected_execution_source` 仍然必须是 `legacy_policy`。

### DiagnosisReadiness

`DiagnosisReadiness` 回答的问题是：诊断类任务在 dry-run 观测中是否具备未来有限 active 的候选条件？

它检查 runtime status、manual/reference、alarm/fault context、analysis basis、stale disclosure、missing evidence、authorization、planning diff 和 shadow tool scope。

它不负责：

- 授权 diagnosis active
- 改变 enabled nodes
- 改变 runtime tools
- 跳过 SQL/RAG/analysis/report/workorder/evidence/output 阶段

`ready_for_active` 在 Phase 4.4.1 始终为 `false`。`alarm_triage` 和 `fault_diagnosis` 可以被标为 `candidate_for_limited_active`，但只是下一阶段评估信号；`root_cause_analysis` 和 `health_assessment` 默认保持 `more_eval` 或 `keep_legacy`。

## 输出面

`/chat/plan` 输出 compact summaries：

- `resolved_context`
- `goal_set`
- `task_family`
- `shadow_plan`
- `planning_diff`
- `planner_gate`
- `diagnosis_readiness`

SSE complete 输出 compact summaries：

- 顶层 `resolved_context`
- 顶层 `planning_diff`
- 顶层 `planner_gate`
- 顶层 `diagnosis_readiness`
- `workflow_route.resolved_context`
- `workflow_route.planning_diff`
- `workflow_route.planner_gate`
- `workflow_route.diagnosis_readiness`
- `decision.shadow_plan_summary`
- `decision.planning_diff_summary`
- `decision.planner_gate_summary`

Trace metadata 只写 compact summaries。

Artifacts 可以 additive 地保存 full shadow plan、full planning diff 或 full planner gate，用于离线 debug，但这些字段不是执行输入。

Compact summaries 会刻意省略 SQL 原文、长 evidence、报告正文、报告 URL 和越权设备细节。

## 验证结果

最近一次验证结果：

- `PYTHONPATH=. pytest -q`：207 passed。
- `PYTHONPATH=. python tests/evals/run_plan_eval.py`：42/42 passed。
- `PYTHONPATH=. python scripts/context_acceptance_test.py`：6 passed。
- `PYTHONPATH=. python scripts/goal_acceptance_test.py`：5 passed。
- `PYTHONPATH=. python scripts/shadow_planner_acceptance_test.py`：5 passed。
- `PYTHONPATH=. python scripts/planning_diff_acceptance_test.py`：7 passed。
- `PYTHONPATH=. python scripts/planner_gate_acceptance_test.py`：11 passed。
- `PYTHONPATH=. python scripts/diagnosis_dry_run_acceptance_test.py`：8 passed。
- `PYTHONPATH=. python scripts/planner_gate_observation_report.py`：report generated。
- `git diff --check`：clean。

Planner gate observation summary：

- total mode-case observations：`126`
- disabled observations：`42`
- dry-run eligible observations：`13`
- active eligible observations：`13`
- selected `planner_gated`：`13`
- selected `legacy_policy`：`113`
- fallback observations：`113`
- active enabled-node changes：`13`
- active runtime-tool changes：`4`
- runtime-tool expansions：`0`
- critical planning diffs：`0`

active 变化都是只读投影。没有 active case 把 runtime tools 扩大到 disabled-mode legacy baseline 之外。

## 当前不建议进入 Active 的能力

以下能力应继续保持 legacy，或仅允许 shadow/dry-run，直到后续阶段单独批准：

- diagnosis root-cause execution
- 带高风险结论的 health assessment
- `action_or_workorder`
- `decide_workorder` active execution
- 没有 refresh/disclosure 的 stale workorder decision
- ambiguous context follow-up
- action follow-up context
- missing auth context
- unauthorized artifact/report/fault-code inheritance
- 显式切换设备但仍引用旧 artifact 的场景
- 任何带有 `needs_review`、`unsafe_mismatch`、`error` 或 `critical` 的 `PlanningDiff`
- 任何会跳过 permission、risk、audit、evidence validation 或 output guardrail 的路径
- 自动派单、复位、启停设备、修改参数，或输出已完成动作语义

Phase 4.4.2 可以进入 diagnosis limited-active 设计评审，但不应在没有单独批准和新 acceptance gates 的情况下开始 diagnosis active migration。action/workorder 仍然不进入 active migration。
