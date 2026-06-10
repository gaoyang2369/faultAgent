---
mode: quick
date: 2026-06-09
task: backend-chat-router-split
---

# Chat 路由拆分计划

## 目标

继续后端重构路线图 Phase 1，把高耦合聊天入口从 `fault_diagnosis/app.py` 迁移到 `fault_diagnosis/api/chat.py`，保持现有 SSE 协议、HTTP 路径、请求参数、响应字段、session/thread 权限过滤和停止流行为不变。

## 范围

- 新增 `fault_diagnosis/api/chat.py`。
- 迁移 `/chat/stream`、`/chat/stream/edit`、`/agent/chat`、`/chat/stop` 及其直接依赖的 payload/helper。
- 在 `fault_diagnosis/app.py` 注册 chat router，并删除已迁移的内联 route/helper/import。
- 更新必要的测试 monkeypatch 目标。
- 更新 `docs/backend-refactor-roadmap.md` 和本轮 summary。

## 不做

- 不修改 SSE event type、payload 字段或错误外壳。
- 不迁移 `/ai/history/*`、`/api/todos/{thread_id}`、静态资源挂载或 agent 初始化生命周期。
- 不重构 `streaming.py` 内部业务逻辑。
- 不读取 `.env`，不升级依赖。

## 验证

- 运行 Python 语法解析和 `compileall` 定向验证。
- 运行 `git diff --check`。
- 当前项目没有可用 Python 3.12/pytest 环境时，不强行跑完整 pytest，只记录阻塞。
