---
mode: quick
date: 2026-05-28
task: log-encoding-mojibake
---

# 后端日志乱码修复总结

## 完成内容

- 新增统一 UTF-8 stdio 初始化，覆盖 Python 模块入口、统一 logger、server runner 和本地脚本入口。
- 保留 JSON 文件日志 UTF-8 写入与 `ensure_ascii=False`，确保中文在 `trash/run/app-json.log` 中直接可读。
- 修复 `fault_diagnosis/app.py`、`fault_diagnosis/session_store.py`、`fault_diagnosis/workflows/steps/knowledge_lookup.py` 中已确认的 mojibake 文本。
- PowerShell 后端/本地开发/测试脚本设置 UTF-8 控制台输出与 Python UTF-8 环境变量。
- README 和 DEPLOY 补充日志编码、Windows 终端和外部日志查看器注意事项。

## 验证

- `py_compile` 通过：本次触达的 Python 文件和新增测试文件。
- 聚焦测试通过：`tests/test_logging_encoding.py`、`tests/test_server_runner.py`。
- workflow step 相关测试通过：`tests/test_workflow_steps.py`、`tests/test_workflow_steps_data.py`、`tests/test_workflow_steps_review.py`。
- `git diff --check` 通过。

## 已知情况

- 可选全量 `pytest -q -x` 的第一个失败为 `tests/test_admin_pdf_pipeline.py::test_rebuild_uploaded_pdf_knowledge_base_falls_back_to_corpus`，实际命中了基础 PDF 知识库而不是上传 PDF 知识库；与本次日志编码和源码 mojibake 修复无关。
