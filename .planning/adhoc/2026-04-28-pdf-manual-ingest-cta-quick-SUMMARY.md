# 2026-04-28 PDF 手动归档 CTA 修复总结

## 已完成
- 上传后默认只做 PDF 登记与轻量文本提取，不再自动把所有文本型 PDF 直接标记为知识库已归档。
- 新增显式归档接口：`POST /admin/pdfs/{record_id}/ingest`。
- 上传记录新增 / 对外暴露：
  - `agent_ingest_status`
  - `agent_query_ready`
  - `kb_error`
- 上传知识库仅收录：
  - 已成功归档的 PDF
  - 当前正在执行归档的 PDF
- 前端已将“浏览器打印导出”替换为“知识库归档 / 重试归档 / 已归档 / 需要 OCR 后归档”等真实 CTA。
- 右侧结果面板新增“Agent 可读取状态”展示。

## 验证
- `pytest -q tests/test_admin_pdf_pipeline.py tests/test_kb_tools.py` 通过。
- `npm run build` 通过。
- 真实闭环脚本已验证：
  - 上传文本型 PDF
  - 提取后状态为 `pending`
  - 点击归档后转为 `succeeded`
  - `query_knowledge_base` 能返回上传 PDF 正文，并带 `file_id/source_type/extract_backend`
  - 删除记录后可清理上传知识库中的对应来源

## 备注
- 本轮仍未启用 `medicineOCR` 重模型。
- 扫描件 / 文本不足 PDF 仍会停在“需要重型 OCR 后归档”。
