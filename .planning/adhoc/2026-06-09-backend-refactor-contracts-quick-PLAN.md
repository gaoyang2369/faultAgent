---
mode: quick
date: 2026-06-09
task: backend-refactor-contracts
---

# 后端重构契约梳理计划

## 目标

在正式拆分后端实现前，先记录当前前端已经依赖的 HTTP API、SSE 事件和后端重构路线，作为后续分层迁移的回归基线。

## 范围

- 梳理 `fault_diagnosis/app.py` 中现有路由的请求、响应和兼容要求。
- 梳理 `fault_diagnosis/streaming.py` 与前端 `api.js` / `useChatStream.ts` 共同消费的 SSE 事件契约。
- 给出后端分层重构路线，明确先契约、再拆 `app.py`、再拆 streaming/agent 核心。

## 不做

- 不修改运行代码。
- 不变更 HTTP 路径、SSE 事件字段或前端调用方式。
- 不读取 `.env`。

## 验证

- 检查新增文档是否能作为后续迁移 checklist 使用。
- 使用 `git diff --check` 检查格式问题。
