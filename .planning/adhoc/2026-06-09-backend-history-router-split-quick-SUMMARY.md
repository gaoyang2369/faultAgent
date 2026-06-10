---
mode: quick
date: 2026-06-09
task: backend-history-router-split
---

# History 路由拆分总结

## 完成内容

- 新增 `fault_diagnosis/api/history.py`，承接 `/ai/history/{type}`、`/ai/history/{type}/{chat_id}`、`DELETE /ai/history/{type}/{chat_id}`、`/api/todos/{thread_id}`。
- `fault_diagnosis/app.py` 改为注册 `history_router`，删除已迁移的 history/todo helper 和内联 route。
- 保留历史列表旧 `thread_id[]` 响应、带 `limit/cursor/q` 时的分页对象、session/thread 归属过滤、legacy thread alias、dev mode 历史和 Todo 行为。
- 更新 `docs/backend-refactor-roadmap.md`，记录 Phase 1 路由拆分已完成，下一步转为补测试和 Phase 2 service 抽取。

## 验证

- 通过：`python -m compileall fault_diagnosis\app.py fault_diagnosis\api tests\test_history_api.py`
- 通过：`git diff --check`，仅输出 Windows 换行提示。
- 阻塞：`python -m pytest tests\test_history_api.py tests\test_smoke.py -q`，当前默认 Python 3.14 环境缺少 `pytest`。
- 阻塞：`conda run -n faultagent ...`，当前 shell 中 `conda` 不在 PATH。

## 后续建议

- 补齐可用的 Python 3.12 `faultagent` 测试环境后，先跑 history、chat、smoke 以及此前已拆 router 的定向测试。
- Phase 2 建议优先抽 `HistoryService`，因为历史列表仍依赖 `checkpointer.alist(None)` 全量扫描，service 层可以先把兼容行为和后续索引优化边界隔离出来。
