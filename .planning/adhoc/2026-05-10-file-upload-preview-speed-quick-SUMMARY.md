---
mode: quick
date: 2026-05-10
task: file-upload-preview-speed
---

# 文档识别校对体验修复总结

## 完成内容

- 将文档识别校对弹窗固定为视口内高度，上传前后保持三列工作区稳定。
- PDF 原文件预览保留在左侧固定面板内，滚轮滚动只浏览 PDF 预览区域，不再撑高整个弹窗。
- PDF 识别改为调用管理员 PDF 上传接口，并用快速轮询等待后端 OCR 状态完成。
- 识别结果返回后立即渲染 Markdown 与文档预览，手动编辑时保留轻量防抖。
- 后端 PDF 详情接口在识别完成后返回已保存的 `kb_markdown`，前端可直接填充完整 Markdown。

## 变更文件

- `agent_fronted/src/views/FileUpload.vue`
- `agent_fronted/src/services/api.js`
- `agent_fronted/src/services/api.d.ts`
- `fault_diagnosis/admin_pdf_registry.py`

## 验证

- `npm.cmd run build` 通过。
- Vite 本地服务已启动并通过 HTTP 探测：`http://127.0.0.1:9005/`。
- `python` / `conda` 未在当前 shell 可用，项目 `.venv` 指向缺失的 WindowsApps Python，因此本轮未能执行 `tests/test_admin_pdf_pipeline.py`。
