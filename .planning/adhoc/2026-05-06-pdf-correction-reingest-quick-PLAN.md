# 2026-05-06 PDF 编辑校正与重新归档闭环计划

## 背景
- PDF 上传、只读预览、状态时间线、显式知识库归档、一键提问已具备。
- 当前仍缺少“编辑校正 -> 保存 -> 旧知识库失效 -> 重新归档 -> Agent 使用校正内容”的真实闭环。

## 目标
1. 保存用户校正文本，同时保留原始 OCR/pypdf 文本、结构化结果和原始 KB Markdown。
2. 保存校正后立即标记该 PDF 待重新归档，并从上传 PDF 知识库中移除旧内容。
3. 重新归档时优先使用校正文本，并在 metadata 中标记 `corrected` / `correction_source`。
4. 前端提供编辑模式、保存/取消、重新归档提示和一键提问拦截。
5. 增补测试覆盖保存校正、状态变更、重新归档、校正内容检索和重复归档。

## 执行范围
- 后端：`fault_diagnosis/admin_pdf_registry.py`、`fault_diagnosis/admin_pdf_processing.py`、`fault_diagnosis/uploaded_pdf_kb.py`、`fault_diagnosis/app.py`
- 前端：`agent_fronted/src/views/FileUpload.vue`、`agent_fronted/src/services/api.js`、`agent_fronted/src/services/api.d.ts`
- 测试：`tests/test_admin_pdf_pipeline.py`

## 非目标
- 不处理身份安全收口。
- 不启用 medicineOCR 重模型。
- 不重构 Agent workflow。
