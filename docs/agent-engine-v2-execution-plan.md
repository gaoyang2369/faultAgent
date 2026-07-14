# Agent Engine V2 canonical cutover 现状

本文原为分阶段实施计划。canonical cutover 已完成，当前不再维护“未来 Phase”清单；真实生产设计、兼容边界和验收命令统一见 [Agent Engine V2 当前生产架构](../fault_diagnosis/agent/README.md)。

当前稳定结论：

- `ConversationTurnCoordinator` 产生唯一 `CanonicalTurnRequest`；
- per-goal authorization/readiness/source resolution 是编译输入；
- `PlanCompiler` 生成 Goal 可追踪的 DAG；
- `ArtifactRoleBinding` 和 `ReportInputSnapshot` 固定运行来源；
- runtime 只执行已知 canonical validated plan version；
- `GoalExecutionResult` 与 `DeliverableResult` 形成 goal-native 输出；
- composite presenter 只读 canonical capability 与 structured content；
- 旧 Frame、workflow 字段和 Deliverable 别名只在 debug/serialization 边界单向生成；
- `/chat/plan` 只读，canonical request 与 execute 共用同一 coordinator 结果；
- Phase 0 Case A-D、strict authority debt 和三组 eval 已进入最终验收。

版本策略：

```text
new write: v2.canonical.validated
historical read: v2.canonical-phase2/3/4.validated
unknown: inspectable but runtime non-executable
```

兼容内容的删除必须同时证明无生产调用、无公共 API 消费、无历史读取依赖、无测试兼容需要，并由 strict scan 证明无引用。否则继续放在明确的 compatibility/debug 包中，不得重新接回 canonical 决策。
