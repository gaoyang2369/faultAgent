# faultAgent 文档索引

`docs/` 只保留当前态契约和架构说明。历史 phase 迁移流水账、旧 shadow/diff/gate 设计和旧权限长草稿已经移除，避免接手者把退役方案误读成当前生产链路。

当前推荐阅读顺序：

1. [后端 README](../fault_diagnosis/README.md)：后端启动、目录、接口、权限、artifact 和扩展约定。
2. [当前架构总览](./current-architecture.md)：跨后端、Agent、上下文、权限、artifact 的简明当前态。
3. [HTTP API 契约](./backend-api-contract.md)：外部路径、请求/响应、cookie 和权限要求。
4. [SSE 事件契约](./sse-event-contract.md)：`/chat/stream` 事件序列和 `complete` payload。

历史与 cutover 记录：

- [Agent Engine V2 canonical cutover 现状](./agent-engine-v2-execution-plan.md)：迁移完成后的稳定结论与兼容删除规则。
- [Agent Engine V2 Phase 0 Baseline](./agent-engine-v2-phase0-baseline.md)：历史 baseline，用于追溯迁移前契约。

维护原则：

- 新文档写当前事实，不写迁移流水账。
- 旧字段只能作为兼容投影描述，不要写成内部决策输入。
- 退役的 shadow/diff/gate 计划不要重新写成当前架构核心。
- API / SSE 变化先更新契约，再改前端或外部调用方。
