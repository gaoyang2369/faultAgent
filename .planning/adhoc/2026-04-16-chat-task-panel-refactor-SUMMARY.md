---
mode: debug
date: 2026-04-16
owner: codex
status: completed
objective: 关停当前服务后，重构聊天页任务展示层级：将右侧任务面板迁移到 assistant 正文下方，将工具链改成默认收起的可折叠明细，并保持 SSE / todos / 历史恢复联动不回退。
---

# Chat Task Panel Refactor 总结

## 已完成
1. 修改开始前已确认 `8000` 与 `9005` 无监听，本轮不是在旧前后端残留进程上叠改。
2. 已确认当前重复来源：thread 级 `todos` 通过右侧 `TaskPanel` 展示任务进度，消息级 `toolEvents` 又在 `ChatMessage` 中逐条表达相同推进过程，信息层级混乱。
3. 已抽出共享任务状态映射层，统一 todos 归一化、summary、状态文案和任务快照生成。
4. 已将任务面板迁移到 assistant 正文下方，作为主任务进度区；任务快照会随 `write_todos`、`/api/todos/{thread}`、`complete.todos` 实时更新，并落入消息缓存用于历史恢复。
5. 已将工具链改成默认收起的折叠明细，只在用户点击“查看执行明细”后展示完整 `tool_start` / `tool_end` 列表。
6. 已增强本地缓存与服务端 history 合并逻辑，刷新后可恢复消息级 `toolEvents` 与 `taskSnapshot`，而不是只恢复正文。
7. 前端已通过 `npx tsc --noEmit` 与 `npm run build`；后端 `/health/real?deep=true` 正常，真实 SSE / 工具 / todos / history / 重启恢复回归已通过。

## 真实验证结果
1. 服务重启后当前运行进程为后端 `python` PID `21968`、前端 `node` PID `2964`。
2. 健康检查仍为 `status=ok`，`SESSION_SECRET` 来源 `local_dev_file`，`faiss` 明确为 `smoke`。
3. 中文简单问答（Unicode-safe）返回“链路正常”；英文简单问答事件顺序为 `start -> token -> complete`。
4. 中文工具链请求真实触发 `5` 次 `tool_start` / `5` 次 `tool_end`；重启前工具线程 `todos.total=3`、`completed=3`。
5. 重启后同 cookie / 同 thread 的 history 仍可读取；Unicode-safe 的中文 follow-up 直接返回 `F01002`，说明上下文连续性仍成立。

## 说明
- 本轮没有修改后端 SSE 协议，只在前端做了同源状态映射与展示层级重构。
- 浏览器自动化工具当前不可用，因此“任务面板下移”和“工具链默认折叠”的最终视觉确认基于组件结构改动、构建通过和真实链路数据验证；后续人工打开页面即可直接检查最终呈现。
