---
mode: quick
date: 2026-06-09
task: backend-refactor-contracts
---

# 后端重构契约梳理总结

## 完成内容

- 新增后端 API 契约文档，记录当前聊天、历史、Todo、身份、管理员 PDF、治理、TTS、健康检查和静态资源路径。
- 新增 SSE 事件契约文档，记录前端依赖的 `start`、`ping`、`token`、`tool_start`、`tool_end`、`complete`、`server_error` 事件字段。
- 新增后端重构路线图，建议先在当前包内完成 `api/`、`services/`、`agent_runtime/` 分层，再逐步确立 workflow 主链路。
- 本轮未修改运行代码，未改 HTTP 路径、SSE 事件字段或前端调用方式。

## 关键结论

- 当前重构应先保持契约不变，采用逐层迁移，而不是直接推倒重写。
- `app.py` 和 `streaming.py` 是第一批需要拆分的重点，但应先拆低风险路由，再处理聊天流。
- `workflows/` 已经具备成为主链路的基础，后续应从 optional path 逐步提升为核心业务流程。
- 测试需要逐步从 patch 内部函数转向 API/SSE/service 契约测试。

## 验证

- 已执行 `git diff --check`。
- 本轮仅新增文档和 GSD 计划/总结，未运行后端或前端测试。
