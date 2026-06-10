---
mode: execute-phase
date: 2026-06-09
task: backend-service-layer-phase2
---

# 后端应用服务层 Phase2 计划

## 目标

按照 `docs/backend-refactor-roadmap.md` Phase 2，把 HTTP router 中的用例级业务逻辑下沉到 `fault_diagnosis/services/`，让 router 主要负责参数解析、权限入口、调用 service 和返回 FastAPI 响应。

## 范围

- 新增 `fault_diagnosis/services/` 包。
- 抽取 `TtsService`、`GovernanceService`、`AdminPdfService`、`HistoryService`、`ChatService`。
- 保持所有现有 HTTP 路径、请求参数、响应字段、cookie/session 行为和 SSE 外壳不变。
- 让 service 可以被测试直接调用，减少测试对 FastAPI route 函数内部实现的 patch。
- 更新后端重构路线图和本轮 GSD summary。

## 不做

- 不拆分 `streaming.py` 事件模型，留到 Phase 3。
- 不改变 checkpointer 存储策略，不解决 `alist(None)` 全量扫描性能问题。
- 不改变 PDF record 字段命名和前端兼容行为。
- 不读取 `.env`，不升级 LangChain、LangGraph、FastAPI 等依赖。

## 验证

- 运行 `python -m compileall fault_diagnosis\app.py fault_diagnosis\api fault_diagnosis\services tests`。
- 运行 `git diff --check`。
- 如当前环境可用，运行后端定向测试；若 pytest/conda 不可用，记录阻塞和已完成的替代验证。
