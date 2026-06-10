---
mode: quick
date: 2026-06-10
task: backend-module-classification-phase55
---

# 后端模块归类 Phase5.5 总结

## 完成内容

- 新增 `fault_diagnosis/repositories/governance_repository.py`，承接文件型治理快照和治理台账 repository。
- 精简 `fault_diagnosis/services/governance_service.py`，保留 payload schema 与应用服务 wrapper，通过 repository 完成持久化。
- 新增 `fault_diagnosis/repositories/admin_pdf_repository.py`，承接文件型 PDF registry repository，并通过 `fault_diagnosis/repositories/__init__.py` 统一导出。
- 新增 `fault_diagnosis/services/admin_pdf_pipeline.py`，承接管理员 PDF OCR、结构化解析、知识库归档、校正和删除后的知识库重建流程。
- 将 `fault_diagnosis/admin_pdf_processing.py` 退化为兼容 facade，避免旧导入立即失效。
- 保留 `fault_diagnosis/admin_pdf_registry.py` 的底层记录/文件函数，并为旧 repository 入口提供懒加载兼容。
- 调整 `/health/dependencies` 的治理和 PDF 检查路径，直接检查 repository 边界；同时让 OCR 健康检查懒加载 OCR runtime，避免依赖缺失时模块导入直接失败。
- 补充 `tests/test_repositories.py`，覆盖治理 repository 和 PDF repository 的轻量行为。
- 更新 `docs/backend-refactor-roadmap.md`，新增 Phase5.5 模块归类状态。

## 验证

- `python -m compileall fault_diagnosis tests`：通过。
- `git diff --check`：通过；仅输出当前工作区已有的 LF/CRLF 换行提示。
- repository / governance import smoke：通过。
- health core smoke：`_check_admin_pdf_registry()`、`_check_governance_repository()`、`_check_medicine_ocr()` 可调用；当前默认 Python 缺 `pypdf` 时 OCR 检查返回 `failed`。

## 未完成验证

- `python -m pytest tests/test_repositories.py tests/test_backend_services.py tests/test_admin_pdf_pipeline.py tests/test_governance_api.py tests/test_health.py -q`：未运行成功，当前默认 Python 为 3.14 且缺少 `pytest`。
- `conda run -n faultagent ...`：未运行成功，当前 shell 未识别 `conda`，常见 `faultagent` / `faultagent312` 路径不存在。
- `fault_diagnosis.api.health` import smoke：未运行成功，当前默认 Python 缺少 `fastapi`。

## 后续建议

- 在 `faultagent` / Python 3.12 环境补跑本轮定向 pytest。
- Phase6 再集中迁移旧测试 patch 目标；本轮保留顶层兼容入口，避免结构整理和测试体系调整混在一起。
