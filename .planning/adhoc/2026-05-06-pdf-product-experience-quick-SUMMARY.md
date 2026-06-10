# 2026-05-06 PDF 上传产品化体验优化总结

## 已完成
- 识别结果区域改为只读预览，移除无保存闭环的输入框、修改标记和结果预览弹窗入口。
- 后端 PDF 记录响应补充 `agent_queryable`、`knowledge_source_type`、`upload_status`、`extract_status`、`last_error`、`updated_at`、`status_timeline`。
- 前端基于 `status_timeline` 展示状态时间线，覆盖上传、文本提取、待归档、归档中、已归档、Agent 可查询、需要重型 OCR、归档失败等节点。
- PDF 详情按钮区调整为上传登记、知识库归档、用此 PDF 提问、删除记录、清除。
- 新增“用此 PDF 提问”入口：仅已归档时向聊天输入框填入带 `PDF id`、`file_name`、`source_type` 的推荐问题；未归档、归档中、需 OCR、归档失败时给出明确提示。
- 上传 PDF 词法检索补充匹配 `file_name`、`file_id`、`source_type`，让一键提问按文件名 / PDF id 更容易召回对应文档。

## 验证
- `C:\miniconda3\envs\faultagent312\python.exe -m pytest -q tests/test_admin_pdf_pipeline.py tests/test_kb_tools.py` 通过。
- `powershell -ExecutionPolicy Bypass -File .\scripts\run_tests.ps1` 通过，298 passed。
- `npm run build` 通过。

## 备注
- 本轮未处理身份安全收口。
- 本轮未启用 medicineOCR 重模型。
- Agent 工作流和知识库协议保持不重构，仅利用现有 `query_knowledge_base` 对上传 PDF 知识库的检索能力。
