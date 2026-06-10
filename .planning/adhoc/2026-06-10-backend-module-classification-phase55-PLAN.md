---
mode: quick
date: 2026-06-10
task: backend-module-classification-phase55
---

# 后端模块归类 Phase5.5 计划

## 目标

在不进入 Phase6 测试体系大迁移的前提下，按当前后端分层规范整理 Phase5 后遗留的模块归属问题，让 repository、service、pipeline 的边界更清晰，并保留旧入口兼容。

## 范围

- 将治理快照/台账文件型 repository 从 `services/governance_service.py` 拆到 `fault_diagnosis/repositories/governance_repository.py`。
- 将管理员 PDF registry repository 从 `admin_pdf_registry.py` 拆到 `fault_diagnosis/repositories/admin_pdf_repository.py`。
- 将管理员 PDF OCR/归档流水线实现归入 `fault_diagnosis/services/admin_pdf_pipeline.py`，保留 `admin_pdf_processing.py` 作为兼容入口。
- 更新 service、health 和 repository 包导出，使调用方向新分层收敛。
- 更新后端重构路线图，补充 Phase5.5 模块归类状态。
- 增加本轮 GSD summary。

## 不做

- 不删除旧顶层兼容入口，避免影响外部导入和现有测试。
- 不改变现有 HTTP 路径、SSE 事件字段、cookie/session 行为。
- 不调整 Phase6 测试体系，不迁移大量测试 patch 目标。
- 不读取 `.env` 内容，不升级依赖，不新增重型依赖。

## 验证

- 运行 `python -m compileall fault_diagnosis tests`。
- 运行 `git diff --check`。
- 优先运行 `tests/test_backend_services.py`、`tests/test_repositories.py`、`tests/test_admin_pdf_pipeline.py`、`tests/test_governance_api.py`、`tests/test_health.py`。
- 若当前环境缺少 pytest 或 Python 3.12，记录阻塞和已完成的替代验证。
