---
mode: quick
date: 2026-06-10
task: backend-package-root-slimming-phase56
---

# 后端包根瘦身 Phase5.6 计划

## 目标

在保持现有 HTTP/SSE 契约和旧导入兼容的前提下，继续整理 `fault_diagnosis/` 包根，将基础设施、认证、知识库、外部集成和 agent 运行时辅助模块归入明确子包，减少顶层文件混排。

## 范围

- 新增 `auth/`、`common/`、`infrastructure/`、`knowledge/`、`integrations/`、`quality/` 等子包。
- 将 `admin_auth.py`、`encoding.py`、`logger.py`、`utils.py`、`db_pool.py`、`server_runner.py`、`knowledge_base.py`、`uploaded_pdf_kb.py`、`medicine_ocr_runtime.py`、`stream_control.py`、`middleware.py`、`error_classification.py`、`evidence.py`、`governance.py`、`safe_actions.py` 的实现搬入归属子包。
- 顶层同名文件保留为兼容 facade，避免测试、脚本和外部调用立即断裂。
- 调整已知内部高价值导入路径，使新分层成为主路径。
- 更新 `docs/backend-refactor-roadmap.md` 和本轮 GSD summary。

## 不做

- 不移动 `app.py`、`config.py`、`paths.py`。
- 不创建 `evidence/` / `governance/` 同名包；质量门禁相关实现统一进入 `quality/`。
- 不删除兼容 facade，不迁移大量测试 patch 目标。
- 不改变 HTTP 路由、SSE 字段、PDF record 字段或 session/cookie 行为。
- 不读取 `.env` 内容，不升级依赖。

## 验证

- 运行 `python -m compileall fault_diagnosis tests`。
- 运行 `git diff --check`。
- 运行可用的 import smoke，覆盖新路径和旧 facade。
- 若 pytest 或项目依赖缺失，记录阻塞。
