---
mode: quick
date: 2026-06-09
task: backend-history-router-split
---

# History 路由拆分计划

## 目标

继续后端重构路线图 Phase 1，把历史与 Todo 入口从 `fault_diagnosis/app.py` 迁移到 `fault_diagnosis/api/history.py`，保持现有 HTTP 路径、响应外壳、session/thread 权限过滤、legacy thread alias、分页兼容模式和 dev mode 行为不变。

## 范围

- 新增 `fault_diagnosis/api/history.py`。
- 迁移 `/ai/history/{type}`、`/ai/history/{type}/{chat_id}`、`DELETE /ai/history/{type}/{chat_id}`、`/api/todos/{thread_id}`。
- 迁移历史分页 helper、thread 标题 helper 和 Todo summary 逻辑。
- 在 `fault_diagnosis/app.py` 注册 history router，并删除已迁移的内联 route/helper/import。
- 更新 `docs/backend-refactor-roadmap.md` 和本轮 summary。

## 不做

- 不修改数据库/checkpointer 访问策略，不在本轮解决 `alist(None)` 全量扫描问题。
- 不修改 `/chat/*`、`/agent/chat`、治理、PDF、TTS、健康检查、静态资源挂载。
- 不改 API 契约字段，不改前端调用路径。
- 不读取 `.env`，不升级依赖。

## 验证

- 运行 Python 语法解析和 `compileall` 定向验证。
- 运行 `git diff --check`。
- 若当前环境仍缺 pytest/FastAPI/conda，则记录阻塞，不临时污染依赖环境。
