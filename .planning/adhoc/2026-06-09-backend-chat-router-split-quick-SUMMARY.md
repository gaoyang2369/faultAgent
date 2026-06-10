---
mode: quick
date: 2026-06-09
task: backend-chat-router-split
---

# Chat 路由拆分总结

## 完成内容

- 新增 `fault_diagnosis/api/chat.py`，承接 `/chat/stream`、`/chat/stream/edit`、`/agent/chat`、`/chat/stop`。
- `fault_diagnosis/app.py` 改为注册 `chat_router`，删除已迁移的 chat payload、helper 和内联 route。
- 保留现有 SSE 协议、HTTP 路径、请求参数、session/thread 权限过滤、编辑重生成历史截断和停止流行为。
- 将 `tests/test_agent_chat_api.py`、`tests/test_chat_edit_api.py` 的 monkeypatch 目标迁移到 `fault_diagnosis.api.chat.token_stream_events`。
- 更新 `docs/backend-refactor-roadmap.md`，记录聊天入口拆分已完成，并把下一步收敛到 history/todo 路由拆分。

## 验证

- 通过：`python -m compileall fault_diagnosis\app.py fault_diagnosis\api tests\test_agent_chat_api.py tests\test_chat_edit_api.py`
- 通过：`git diff --check`，仅输出 Windows 换行提示。
- 阻塞：`python -m pytest tests\test_agent_chat_api.py tests\test_chat_edit_api.py tests\test_smoke.py -q`，当前默认 Python 3.14 环境缺少 `pytest`。
- 阻塞：`conda run -n faultagent ...`，当前 shell 中 `conda` 不在 PATH。
- 阻塞：Codex runtime Python 3.12.13 可用，但缺少 `pytest` 和 `fastapi`，无法执行 HTTP 导入级测试。

## 后续建议

- 补齐可用的 Python 3.12 `faultagent` 测试环境后，优先跑 chat 定向测试和 smoke 测试。
- 下一轮继续 Phase 1，拆分 `fault_diagnosis/api/history.py`，迁移 `/ai/history/*` 与 `/api/todos/{thread_id}`。
