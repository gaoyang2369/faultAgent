---
mode: debug
date: 2026-04-16
owner: codex
status: completed
objective: 关停当前服务后，重构聊天页任务展示层级：将右侧任务面板迁移到 assistant 正文下方，将工具链改成默认收起的可折叠明细，并保持 SSE / todos / 历史恢复联动不回退。
---

# Chat Task Panel Refactor 计划

## 本轮范围
- 审计当前前后端启动方式、端口、服务状态，确保修改前无残留运行服务。
- 梳理聊天页中 todos、tool events、history cache、SSE 回调之间的数据流关系，识别重复展示与单一事实源。
- 将现有任务面板从侧边栏迁移到 assistant 消息正文下方，作为主任务进度视图。
- 将原有工具链区域改造成默认收起的可折叠明细视图，用于展示 tool_start / tool_end / 原始工具反馈。
- 启动前后端并做真实 SSE、工具、历史恢复、重启恢复回归。

## 执行策略
1. 先确认当前 8000 / 9005 无监听，若有残留则安全关停；不在旧进程上直接叠改。
2. 审查 `CustomerService.vue`、`ChatMessage.vue`、`TaskPanel.vue`、`useTodosPanel.ts`、`useChatStream.ts`、`api.js` 等关键链路。
3. 抽离共享任务状态映射层，保证 todos 的归一化 / summary / 文案 / 装饰在一个地方维护。
4. 在消息模型上为 assistant 增加任务快照读模型，让任务面板和工具链都基于同一底层事件衍生，而不是各自维护独立状态。
5. 保持现有 SSE 协议不变，优先在前端做最小侵入聚合；仅在必要时补充字段。
6. 启动前后端并做简单问答、工具调用、todos 更新、刷新恢复、重启恢复回归。

## 当前审计结论
1. 当前最常用启动脚本为 `scripts/run_backend.ps1` 和 `scripts/run_frontend_dev.ps1`。
2. 修改开始前，`8000` 与 `9005` 均未监听，未发现正在运行的前后端服务。
3. 当前重复主要来自：右侧 `TaskPanel` 使用 thread 级 todos 展示执行进度，而 `ChatMessage` 中的工具链又在逐条展示同一轮任务推进。
4. 当前任务状态的事实源仍是后端 `todos` / `/api/todos/{thread}` 与 SSE 中 `write_todos` 结果；工具链是事件明细视图，不应继续承担主任务摘要职责。
