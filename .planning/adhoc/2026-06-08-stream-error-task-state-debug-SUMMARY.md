---
mode: debug
date: 2026-06-08
task: stream-error-task-state
---

# 流式失败后任务状态未收尾调试总结

## 发现

- 截图中的“回复失败”来自前端收到后端 `server_error`。
- 用户中断路径会调用 `interruptTaskSnapshot`，把进行中的任务标为中断。
- 错误路径只更新聊天气泡的 `streamState` 和 `statusText`，没有处理已有 `taskSnapshot`，导致任务面板继续显示 `in_progress`。

## 修复

- 在 `useChatStream.ts` 增加失败收尾 helper。
- `onError` 和发送异常路径都会把当前任务快照中断为“执行已停止”。
- 在 `chatMessageModel.js` 增加历史消息归一化兜底，避免旧缓存中的失败消息继续展示 `in_progress`。
- 将新构建产物同步到 `agent_fronted/public`，因为后端静态服务挂载的是 `public` 而不是 `dist`。
- 保留上一轮已存在的同步防重复发送锁，不回滚已有改动。

## 验证

- `node agent_fronted/src/utils/chatMessageModel.test.mjs` 通过。
- `cmd /c npm run build` 通过。
- `git diff --check` 通过，仅提示 CRLF/LF 工作区换行警告。
