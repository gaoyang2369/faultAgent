---
mode: execute-phase
date: 2026-06-10
task: backend-persistence-performance-phase5
---

# 后端持久化与性能治理 Phase5 总结

## 完成内容

- 新增 `fault_diagnosis/repositories/history_index.py`，提供文件型与内存型 history index repository。
- `HistoryService.list_history()` 改为读取 history index，不再在历史列表路径调用 `checkpointer.alist(None)`；详情命中 checkpoint 时会补记索引，删除历史时同步清理索引和 workflow artifact。
- `ChatService` 在 `/chat/stream` 与 `/chat/stream/edit` 接受请求后登记历史索引，保持现有 thread/session 权限边界不变。
- 新增 `FileArtifactStoreBackend`，`workflows.artifact_store` 默认走文件系统后端；测试环境仍默认内存后端，显式 `WORKFLOW_ARTIFACT_BACKEND=postgres` 仍可使用。
- 治理快照和台账收敛到 `FileGovernanceRepository`，`GovernanceService` 保留为应用服务 wrapper。
- PDF registry 增加 `FileAdminPdfRepository`，`AdminPdfService` 通过 repository 访问记录和文件路径。
- `/health/dependencies` 新增 `history_index`、`workflow_artifact_store`、`governance_repository`、`admin_pdf_registry` 检查项。
- 新增 `tests/test_repositories.py`，并将历史 API 测试从保护 `alist(None)` 调用细节调整为保护索引读取和 session 边界。
- 更新 `docs/backend-refactor-roadmap.md`，同步 Phase5 进度、状态和下一步建议。

## 验证

- `python -m compileall fault_diagnosis tests`：通过。
- `git diff --check`：通过；仅输出当前工作区已有 LF/CRLF 换行提示。

## 未完成验证

- `python -m pytest tests/test_repositories.py tests/test_backend_services.py tests/test_history_api.py tests/test_health.py -q`：未运行，当前默认 Python 为 3.14 且缺少 pytest。

## 后续建议

- 在 `faultagent` / Python 3.12 环境补跑 Phase1-5 定向测试。
- 进入 Phase6 时，继续把测试从旧模块内部 patch 迁移到 API 契约、SSE 契约、service 和 repository 边界。
