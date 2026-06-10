---
mode: debug
date: 2026-06-08
task: stream-error-task-state
---

# 流式失败后任务状态未收尾调试计划

## 目标

定位 SSE 回复失败后，聊天气泡已经停止但任务进度仍显示执行中的原因，并修复前端状态收尾。

## 范围

- 检查前端 `server_error`、发送异常、用户中断三条路径对 `taskSnapshot` 的处理差异。
- 保持现有 HTTP 接口和 SSE 事件契约不变。
- 不读取 `.env` 内容，不改数据库配置。

## 验证

- 运行前端生产构建。
- 运行 `git diff --check`。
