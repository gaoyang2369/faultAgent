# faultAgent 文档索引

`docs/` 只保留当前态契约和架构说明。历史 phase 迁移流水账、旧 shadow/diff/gate 设计和旧权限长草稿已经移除，避免接手者把退役方案误读成当前生产链路。

当前推荐阅读顺序：

1. [后端 README](../fault_diagnosis/README.md)：后端启动、目录、接口、权限、artifact 和扩展约定。
2. [single_agent legacy rollback README](../fault_diagnosis/single_agent/README.md)：旧 runner 的短期回滚边界，以及仍被 V2 复用的 helper。
3. [当前架构总览](./current-architecture.md)：跨后端、Agent、上下文、权限、artifact 的简明当前态。
4. [HTTP API 契约](./backend-api-contract.md)：外部路径、请求/响应、cookie 和权限要求。
5. [SSE 事件契约](./sse-event-contract.md)：`/chat/stream` 事件序列和 `complete` payload。

目标态规划：

- [Agent Engine V2 架构迁移执行计划](./agent-engine-v2-execution-plan.md)：用于后续分阶段迁移执行，不代表当前生产链路已经具备这些能力。
- [Agent Engine V2 Phase 0 Baseline](./agent-engine-v2-phase0-baseline.md)：V2 迁移前的当前契约、继承矩阵、baseline case list 和 feature flag 草案。

维护原则：

- 新文档写当前事实，不写迁移流水账。
- 旧字段只能作为兼容投影描述，不要写成内部决策输入。
- 退役的 shadow/diff/gate 计划不要重新写成当前架构核心。
- API / SSE 变化先更新契约，再改前端或外部调用方。
