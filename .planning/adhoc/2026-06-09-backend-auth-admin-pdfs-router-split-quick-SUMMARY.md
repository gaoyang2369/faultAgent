---
mode: quick
date: 2026-06-09
task: backend-auth-admin-pdfs-router-split
status: completed-with-test-env-blocker
---

# Auth 和 Admin PDF 路由拆分总结

## 已完成

- 新增 `fault_diagnosis/api/_shared.py`，承接 session scope cookie 写入、管理员身份解析和管理员权限检查。
- 新增 `fault_diagnosis/api/auth.py`，迁移 `/auth/identity`、`/auth/admin/login`、`/auth/logout`。
- 新增 `fault_diagnosis/api/admin_pdfs.py`，迁移 `/admin/pdfs`、`/admin/pdfs/{record_id}`、`/admin/pdfs/{record_id}/file`、`/admin/pdfs/{record_id}/ingest`、`/admin/pdfs/{record_id}/correction` 和删除接口。
- `fault_diagnosis/app.py` 注册 `auth_router`、`admin_pdfs_router`、`tts_router`、`health_router`，并删除已迁移的内联 route/helper/import。
- `docs/backend-refactor-roadmap.md` 已更新 Phase 1 进度，下一步推进到治理路由拆分。

## 契约影响

- HTTP 路径、方法、状态码、响应外壳未变。
- `fd_session`、`fd_legacy_threads`、`fd_admin_auth` 的写入和清理逻辑保持由服务端执行。
- 管理员 PDF 接口仍统一要求管理员身份，文件响应仍附加 session scope cookie。
- 聊天、历史、Todo、治理、SSE、静态资源挂载未迁移。

## 验证

- `python -m compileall -q fault_diagnosis/app.py fault_diagnosis/api tests/test_admin_auth.py tests/test_admin_pdf_pipeline.py`：通过。
- AST 解析 `fault_diagnosis/app.py`、`fault_diagnosis/api/_shared.py`、`fault_diagnosis/api/auth.py`、`fault_diagnosis/api/admin_pdfs.py`：通过。
- `git diff --check`：通过，仅提示工作区 LF/CRLF 规范化 warning。

## 未执行

- 未运行 pytest。当前项目没有可用的仓库要求 Python 3.12/pytest 测试环境，本轮按用户要求只做语法验证。

## 后续

- 恢复 Python 3.12 环境后优先补跑 `tests/test_health.py`、`tests/test_tts_synthesize_api.py`、`tests/test_admin_auth.py`、`tests/test_admin_pdf_pipeline.py`、`tests/test_smoke.py`。
- 下一轮继续拆 `fault_diagnosis/api/governance.py`，迁移 `/api/governance/*`。
