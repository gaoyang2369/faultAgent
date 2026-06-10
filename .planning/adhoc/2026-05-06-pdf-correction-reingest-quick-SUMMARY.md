# 2026-05-06 PDF 编辑校正与重新归档闭环总结

## 已完成
- 新增 `PATCH /admin/pdfs/{record_id}/correction`，保存用户校正文本并保持管理员校验。
- 校正内容独立保存到 `corrections/`，原始 OCR 文本、结构化结果和原始 KB Markdown 不被覆盖。
- `structured_results` 追加 `corrected_result` 元数据，记录校正文本、时间、来源和版本。
- 保存校正后将 PDF 标记为待重新归档 / `agent_ingest_status=stale`，并重建上传 PDF 知识库，旧 corpus 不再作为有效来源。
- 重新归档时优先读取校正文本，并在上传 PDF 知识库 metadata 中写入 `corrected`、`correction_source`、`correction_version`。
- 前端新增编辑校正模式、保存校正、取消、保存后重新归档提示和一键提问拦截。
- 状态时间线新增人工校正、等待重新归档、校正内容已归档、重新归档失败相关节点。

## 验证
- `C:\miniconda3\envs\faultagent312\python.exe -m pytest -q tests/test_admin_pdf_pipeline.py tests/test_kb_tools.py` 通过，11 passed。
- `powershell -ExecutionPolicy Bypass -File .\scripts\run_tests.ps1` 通过，299 passed。
- `npm run build` 通过。

## 备注
- 本轮未处理身份安全收口。
- 本轮未启用 medicineOCR 重模型。
- 未修改 `.planning/adhoc/2026-05-06-leading-branch-test-fix-debug-SUMMARY.md`。
