---
mode: quick
date: 2026-06-09
task: backend-governance-router-split
---

# Governance 路由拆分计划

## 目标

继续后端重构路线图 Phase 1，把 `/api/governance/*` 从 `fault_diagnosis/app.py` 迁移到 `fault_diagnosis/api/governance.py`，保持现有 HTTP 路径、请求体、响应字段、文件落盘路径和 session scope cookie 行为不变。

## 范围

- 新增 `fault_diagnosis/api/governance.py`。
- 迁移治理快照、治理列表、治理台账创建、治理台账查询、治理台账更新相关 Pydantic payload、helper 和 route。
- 在 `fault_diagnosis/app.py` 注册 governance router，并删除已迁移的内联 route/helper/import。
- 更新 `docs/backend-refactor-roadmap.md` 和本轮 summary。

## 不做

- 不修改 `/api/governance/*` 的 HTTP 契约。
- 不迁移聊天、历史、Todo、SSE、静态资源和 agent 运行时逻辑。
- 不读取 `.env`，不升级依赖。

## 验证

- 运行 Python 语法解析和 `compileall` 定向验证。
- 运行 `git diff --check`。
- 当前项目没有可用 Python 3.12/pytest 环境时，不强行跑完整 pytest，只记录阻塞。
