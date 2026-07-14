# 当前架构总览

faultAgent 是工业设备故障诊断系统：后端源码位于 `fault_diagnosis/`，前端位于 `agent_fronted/`。生产环境只有 Agent Engine V2 单 Agent 链路，不存在 legacy runtime、双轨 planner 或多 Agent 编排。

## Canonical 主链

```text
HTTP/SSE + trusted AuthContext + thread context
  -> ProductionTurnCoordinator
  -> CurrentUtteranceParse
  -> CanonicalTurnRequest
  -> PendingClarification transition preview/commit
  -> per-goal authorization / readiness / source resolution
  -> Canonical Goal -> validated ExecutionPlan DAG
  -> ArtifactRoleBinding + exact artifact loading
  -> typed nodes + EvidenceLedger
  -> GoalExecutionResult
  -> DeliverableResult / composite output
  -> SSE, artifact persistence, trace, optional compatibility projection
```

`IntentFrame`、`RewriteFrame`、`EffectiveRequestFrame`、旧 skill/workflow 字段只存在于历史组件接口、trace 或 `compatibility_debug`。它们从 canonical 结果单向投影，不能参与路由、编译、验证、运行或回答选择。

详细合同、pending 状态机、版本兼容、artifact binding、ReportInputSnapshot、exact loading、输出边界和 Case A-D 解决方式见 [Agent Engine V2 当前生产架构](../fault_diagnosis/agent/README.md)。

## 后端分层

```text
server/    HTTP/SSE、session、auth、幂等、用例编排
agent/     canonical planning、DAG runtime、evidence、output
domain/    canonical turn、artifact、诊断、上下文、权限领域合同
platform/  persistence、tools、knowledge、observability、settings
shared/    无业务语义的通用工具
```

- HTTP 层不推断诊断语义；
- `agent/` 不管理 Web session；
- `domain/` 不依赖 server；
- `platform/` 提供持久化与外部能力，不拥有 Goal 决策权。

## Runtime 与权限

typed nodes 包括 `sql`、`rag`、`analysis`、`comparison`、`report`、`workorder`、`approval`。SQL 只读且受 table/asset ACL 约束；知识库证据不能冒充实时状态；工单只生成建议或草稿，不自动派发；系统不执行设备控制、告警关闭或配置写入。

授权只信任服务端解析的 `AuthContext`。用户提交的展示身份不参与权限判断。授权、就绪和来源解析均按 Goal 独立进行，因此一个 Goal 被拒绝不会抹掉其他独立结果。

## Artifact 与多轮上下文

artifact 可使用 file、memory 或 postgres backend。运行输入必须由 `ArtifactRoleBinding` 指向 exact artifact ID，并校验 thread、owner、设备、类型和完整 lineage。

`ArtifactLineage.source_artifact_ids` 只用于审计追溯；runtime 不会递归选择“兼容祖先”。报告读取绑定 Analysis artifact 内的 `ReportInputSnapshot`，不会重新猜测 SQL 来源或静默升级历史 artifact。

PendingClarification 以 goal-scoped 状态机保存：纯槽位回复恢复原 Goal；混合回复恢复原 Goal 后再追加当前显式 Goal；preview 不提交 pending 或写 artifact。

## 输出与兼容边界

canonical deliverable 字段是 `goal_id`、`capability`、`status`、`structured_content`、`artifact_ids`、`evidence_ids`、`claim_ids`。presenter 只读取这些字段。

历史 complete payload 所需的 `deliverable_type`、`payload`、`source_artifact_ids` 由独立 `LegacyDeliverableProjection` 在序列化边界生成，带 `compatibility_only=true`。旧字段不能反向写入 canonical model。

`/chat/plan` 顶层以 canonical request、goals、per-goal decisions、execution plan 和 artifact bindings 为主；旧 Frame 和 workflow projection 统一放在 `compatibility_debug`，接口保持绝对只读。

## 稳定版本

新 ExecutionPlan 只写 `v2.canonical.validated`。历史 `v2.canonical-phase2/3/4.validated` 继续可读、可执行，并在 trace 中规范化为稳定版本；未知版本只允许查看，runtime 明确拒绝，不原地重写历史 plan/artifact。

## 验收门槛

默认 pytest 收集 Case A-D、pending、exact source、composite output、版本、preview schema 和 strict debt 回归。`tests/red_baseline` 是历史验收集，不是唯一覆盖。`legacy_authority_debt_allowlist.json` 保持空数组，严格扫描必须持续为零。

```bash
PYTHONPATH=. pytest -q
PYTHONPATH=. pytest -q tests/red_baseline
PYTHONPATH=. python tests/evals/run_plan_eval.py --tier core
PYTHONPATH=. python tests/evals/run_context_goal_regressions.py
PYTHONPATH=. python tests/evals/run_canonical_output_regressions.py
PYTHONPATH=. python scripts/legacy_dependency_scan.py --strict --json
PYTHONPATH=. python scripts/goal_native_cutover_check.py --strict
```
