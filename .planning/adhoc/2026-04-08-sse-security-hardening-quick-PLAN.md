---
mode: quick
date: 2026-04-08
owner: codex
status: completed
objective: 在不做无关重构的前提下，修复 SSE 界面与会话链路的高优先级风险，补齐最小可验证的安全边界、连接生命周期与事件语义。
---

# SSE / 会话链路高优先级修复计划

## 本轮范围
- 删除前端动态求值逻辑（`new Function` / `eval`）
- 收紧富文本渲染面，移除危险事件属性放行
- 后端错误仅返回通用消息与 `error_id`
- EventSource 改为单活连接，显式关闭，阻断危险自动重连
- 移除当前无服务端支持的 WebSocket 占位链路
- 补齐前端对 `start` / `ping` / `tool_*` / `complete` / `server_error` / `result_preview` 的消费
- 实现最小可行的服务端会话作用域，避免直接信任前端 `thread_id` / `chat_id`

## 实施策略
1. 先修渲染与错误回传，立即收口最高风险执行面。
2. 再修流式连接生命周期和前端状态机，避免重复连接、残留回调与副作用重放。
3. 最后补最小会话隔离：服务端维护 session cookie 与 thread ownership，历史和 todos 只在服务端确认的作用域内读取。
4. 保持现有 HTTP 入口可用，不做大规模协议重写。

## 验证清单
- 前端 `npm run build`
- 后端 pytest：SSE、history/todos、错误输出、会话隔离
- 人工代码验证：不存在 `new Function` / `eval` / `onclick` 白名单
