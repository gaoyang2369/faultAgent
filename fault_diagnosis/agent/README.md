# Agent Engine V2 当前生产架构

本目录实现一个受限、可审计的单 Agent。生产链只有一条：当前消息先形成 `CurrentUtteranceParse` 与 `CanonicalTurnRequest`，随后对每个 Goal 独立做授权、就绪和来源解析，再编译为有界 DAG。模型、旧 Frame 和兼容字段都不能替代这条权威链。

```text
CurrentUtteranceParse
  -> CanonicalTurnRequest
  -> per-goal authorization / readiness / source resolution
  -> Canonical Goal -> validated ExecutionPlan DAG
  -> ArtifactRoleBinding -> exact artifact loading
  -> typed nodes -> GoalExecutionResult
  -> DeliverableResult -> composite output
  -> optional compatibility/debug serialization
```

## 1. 唯一生产入口

HTTP/SSE 请求的真实路径是：

```text
server/http/routers/chat.py
  -> ChatService
  -> ProductionTurnCoordinator
  -> ConversationTurnCoordinator
  -> AgentEngineV2.build_plan_snapshot
  -> prepare_v2_execution_plan
  -> WorkflowRuntimeExecutor
  -> OutputFrame / SSE / artifact persistence
```

`ProductionTurnCoordinator` 负责 thread、幂等、持久化和 preview/execute 协调；`ConversationTurnCoordinator` 是本轮 canonical request 和 pending 状态转换的唯一权威；`AgentEngineV2` 只把已确定的 canonical 结果编译成计划快照。

`IntentFrame`、`RewriteFrame`、`EffectiveRequestFrame` 以及 `primary_intent`、`primary_skill` 仍可出现在历史测试、trace 或 `compatibility_debug` 中，但它们由 canonical 结果单向生成，不参与生产路由、编译、校验、运行或回答选择。

## 2. CurrentUtteranceParse 与 CanonicalTurnRequest

`CurrentUtteranceParse` 只描述当前一句话：clause、实体、引用、修正表达和解析来源。它不从历史中偷偷补齐设备，也不决定权限。

`CanonicalTurnRequest` 合并当前解析结果、允许恢复的 pending 状态和已验证上下文，包含：

- 固定顺序的 `CanonicalGoal`；
- 每个 Goal 的 origin、clause index、required/missing/resolved slots；
- 当前实体、pending binding 和请求元数据；
- 稳定的 Goal ID、依赖和用户可见性。

同一个 coordinator 结果同时供 `/chat/plan` 与 execute 使用，因此 preview 和 execute 的 canonical request 不会各自重算出不同语义。

## 3. PendingClarification 状态机

缺少必要槽位时，系统创建 `PendingClarification`，状态转换由 repository 的 CAS、幂等键和版本控制保护。恢复分两类：

- `slot_only`：如“我说的是 G120电机2”，只补槽并恢复原诊断 Goal；
- `mixed`：如“是电机2，顺便生成报告”，恢复原诊断 Goal，并按当前 clause 追加报告 Goal。

恢复不会把诊断降级成状态查询；重复消息不会重复消费 pending；preview 只计算转换预览，不提交状态。

## 4. Per-goal 决策

每个 canonical Goal 都有三份独立决定：

1. `GoalAuthorizationDecision`：能力与资源范围是否允许；
2. `GoalReadinessDecision`：槽位、依赖和来源是否已经满足；
3. `GoalSourceResolution`：需要执行、可由精确 artifact 满足、可作为执行来源，或因 stale/ambiguous/missing 被阻断。

一个 Goal 失败、拒绝或缺来源，不会删除其他独立 Goal。依赖 Goal 可以有执行终态，但不会产生额外用户 deliverable。

## 5. Canonical Goal 到 DAG

`PlanCompiler` 只读取 `CanonicalTurnRequest` 与上述 per-goal decisions。它保持 Goal 数量、顺序、origin、clause index 和依赖不变，再按 capability 生成 `sql`、`rag`、`analysis`、`comparison`、`report`、`workorder`、`approval` 节点。

`PlanValidator` 检查：

- Goal 是否完整且顺序未变；
- 节点是否绑定真实 Goal；
- denied、blocked 或已由 artifact 满足的 Goal 是否被错误执行；
- DAG 是否无环；
- 工具、planned artifact ID 和 runtime input 是否有效；
- Artifact role 的类型、基数、生产者和依赖边是否一致。

新计划统一写 `v2.canonical.validated`。`v2.canonical-phase2.validated`、`phase3`、`phase4` 只作为历史读取兼容；运行 trace 同时记录实际读取版本、规范化版本和兼容模式。未知版本可解析查看，但 runtime 拒绝执行，历史数据不会被原地改写。

## 6. ArtifactRoleBinding

节点输入不靠“最近一个看起来像”的 artifact。每个输入使用 `ArtifactRoleBinding` 明确：

- goal/node identity；
- role；
- exact artifact ID 和 type；
- producer goal/node；
- required、device 和 member order。

典型角色包括 `runtime_sql_source`、`knowledge_source`、`comparison_member`、`report_source`、`tabular_source` 和 `workorder_source`。比较节点要求稳定的设备与顺序；报告要求一个精确 analysis source，且最多一个 tabular source。

## 7. ReportInputSnapshot

可报告的 Analysis artifact 内嵌 `ReportInputSnapshot`，冻结报告需要的数据窗口、来源 SQL artifact、freshness 和结构化输入。报告节点必须读取被绑定 Analysis artifact 的 snapshot；不能递归猜祖先，也不能用“最新 artifact”替换指定来源。

旧 Analysis artifact 缺 snapshot 时不会在运行时偷偷升级。需要迁移时使用显式迁移工具生成新 artifact，原 artifact 保持不变。

## 8. Exact loading 与 audit lineage

运行输入只使用 `load_exact_artifact` 和 `ArtifactRoleBinding`，并校验 thread、owner、设备、类型、持久化和完整 lineage。

`source_artifact_ids` 属于 `ArtifactLineage` 的审计血缘，可用于追溯祖先；它不是 runtime source selector。`load_artifact_lineage_for_audit` 可以遍历 lineage，但不得把祖先提升为节点输入。

## 9. GoalExecutionResult 与 DeliverableResult

每个 Goal 都形成 `GoalExecutionResult`，记录计划/执行节点、artifact/evidence/claim IDs、是否由 artifact 满足、阻断来源和错误。

每个用户 Goal 恰好形成一个 canonical `DeliverableResult`：

```text
goal_id
capability
status
structured_content
artifact_ids
evidence_ids
claim_ids
```

assembler 和 presenter 只读取这些 canonical 字段。旧字段 `deliverable_type`、`payload`、`source_artifact_ids` 不在 canonical model 中，而由独立 `LegacyDeliverableProjection` 在 SSE complete 序列化边界单向生成，并带 `compatibility_only=true`。

## 10. Composite output

`CompositePresenter` 按 clause index 和 Goal 顺序渲染。单 Goal variant 由 canonical capability 唯一决定；多 Goal 固定为 `composite_answer`。一个 Goal 失败不会吞掉独立成功结果，依赖失败只阻断依赖链。

回答的证据只来自归属于该 Goal 的 evidence/claims。未绑定证据不能泄漏到其他 deliverable。`requested_variant` 已删除，旧调用值不会进入 answer variant 决策。

## 11. Preview 与 compatibility 边界

`/chat/plan` 是绝对只读接口，主 schema 是：

```text
canonical_request
goals
goal_authorization
goal_readiness
goal_source_resolution
pending_transition
execution_plan
artifact_role_bindings
```

旧 Frame、skill route、旧 workflow 字段位于 `compatibility_debug`，并带 `compatibility_only=true`。它们不与 canonical 字段平级，也不会被 execute 读回。

SSE、历史 complete payload 和 trace 允许保留兼容投影，但兼容数据流只能是：

```text
canonical state -> optional compatibility projection
```

禁止反向从 compatibility 字段推导 canonical 决策。

## 12. 四个原始失败的结构性解决

- Case A：报告 Goal 通过精确 Analysis binding 与 `ReportInputSnapshot` 复用来源，第三轮只有 report node，不重复 SQL/Analysis。
- Case B：parser 保留四个 clause Goal；per-goal decisions、execution results 和 deliverables 一一对应，最终按顺序输出 `composite_answer`。
- Case C：goal-scoped pending 恢复原诊断 capability；slot-only 只恢复诊断，mixed 恢复诊断并追加报告。
- Case D：fault-code explanation 的 required slots 不含设备；`A07089`、`查询 A07089` 和详细解释都形成单一 explanation deliverable。

默认 pytest 已收集这些回归，`tests/red_baseline` 仅保留为历史验收集。strict authority scan 和 goal-native cutover check 必须持续为零债务。

## 13. 运行与验收

```bash
PYTHONPATH=. pytest -q
PYTHONPATH=. pytest -q tests/red_baseline
PYTHONPATH=. python tests/evals/run_plan_eval.py --tier core
PYTHONPATH=. python tests/evals/run_context_goal_regressions.py
PYTHONPATH=. python tests/evals/run_canonical_output_regressions.py
PYTHONPATH=. python scripts/legacy_dependency_scan.py --strict --json
PYTHONPATH=. python scripts/goal_native_cutover_check.py --strict
```

`legacy_authority_debt_allowlist.json` 保持空数组。扫描器仍保留：任何新的 legacy authority read 都会让 strict check 失败。
