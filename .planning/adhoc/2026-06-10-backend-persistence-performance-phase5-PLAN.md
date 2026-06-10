---
mode: execute-phase
date: 2026-06-10
task: backend-persistence-performance-phase5
---

# 后端持久化与性能治理 Phase5 计划

## 目标

按照 `docs/backend-refactor-roadmap.md` Phase 5，减少历史接口全量 checkpoint 扫描和文件散写，让历史、artifact、治理台账、PDF registry 的读写入口更清晰、可替换，并补充健康检查。

## 范围

- 设计并落地文件系统默认实现的 history index，用于历史列表分页，避免常规分页路径依赖 `checkpointer.alist(None)` 全量扫描。
- 将 workflow artifact、治理快照/台账、PDF registry 的文件读写收敛到 repository 风格接口。
- 保持现有 HTTP 路径、响应字段、SSE complete 字段、cookie/session 行为兼容。
- 为 `/health/dependencies` 增加 artifact store 与 history index 的轻量健康检查。
- 补充或调整针对 service/repository 的单元测试。
- 更新后端重构路线图与本轮 GSD summary。

## 不做

- 不引入 Postgres 后端实现，只保留接口扩展点。
- 不升级 LangChain、LangGraph、FastAPI 等依赖。
- 不改变 PDF record 字段命名、治理 API 响应外壳或历史接口兼容模式。
- 不读取 `.env` 内容。

## 验证

- 运行 `python -m compileall fault_diagnosis tests`。
- 运行 `git diff --check`。
- 优先运行 `tests/test_backend_services.py`、`tests/test_history_api.py`、`tests/test_health.py`、`tests/test_admin_pdf_pipeline.py`、`tests/test_governance_api.py` 与新增 repository/history index 测试。
- 若当前环境缺少 pytest、FastAPI 或 Python 3.12，记录阻塞和已完成的替代验证。
