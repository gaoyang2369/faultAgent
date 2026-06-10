---
mode: debug
date: 2026-04-27
task: medicine-ocr-pdf-kb-integration
base_branch: Code-refactoring-version
working_branch: integration-medicine-ocr-pdf-kb
---

# medicineOCR 审计与轻量 PDF->文本提取->知识库底座集成总结

## 审计结论

- `medicineOCR` 当前不是可直接接入的完整 PDF OCR 流水线，而是三段分散脚本：
  - `ocr_test.py`：DeepSeek OCR 图片识别包装，强依赖本地模型目录 + `modelscope` + CUDA
  - `extract.py`：YOLO 签名区域提取与涂白
  - `topdf.py`：Markdown + 签名回填到 PDF，依赖 GTK / WeasyPrint 运行时
- 当前仓库内没有 DeepSeek OCR 本地模型目录，也没有 PDF 分页渲染总入口。
- 当前环境缺少 `modelscope` / `opencv-python` / `ultralytics` / `PyMuPDF`，原始 `medicineOCR` 无法直接运行。
- `medicineOCR` 目录中存在不应入库的大文件（如 `yolobest.pt`、GTK runtime 安装包），本轮已通过 `.gitignore` 排除。

## 本轮实现

- 未移动 `medicineOCR/` 原目录；改为在后端新增可探测、可降级封装：
  - `fault_diagnosis/medicine_ocr_runtime.py`
  - `fault_diagnosis/admin_pdf_processing.py`
  - `fault_diagnosis/uploaded_pdf_kb.py`
- 管理员 PDF 记录扩展为持久化 OCR / 知识库状态：
  - `ocr_status`
  - `ocr_error`
  - `ocr_backend`
  - `ocr_result_file`
  - `structured_result_file`
  - `kb_ingest_status`
  - `kb_document_id`
  - `kb_index_mode`
  - `processed_at`
- 新增轻量配置项（默认不启用重模型）：
  - `PDF_TEXT_EXTRACT_BACKEND=auto`
  - `MEDICINE_OCR_BACKEND=auto`
  - `MEDICINE_OCR_ENABLE_HEAVY_MODEL=false`
  - `MEDICINE_OCR_MODEL_DIR=`
  - `MEDICINE_OCR_DEVICE=auto`
  - `MEDICINE_OCR_TIMEOUT_SECONDS=300`
  - `MEDICINE_OCR_MAX_PAGES=1`
  - `MEDICINE_OCR_RENDER_DPI=120`
  - `PDF_TEXT_MIN_CHARS=100`
- 上传后由后端后台任务自动执行：
  1. 保存原始 PDF
  2. 先走 `pypdf_text` 轻量文本提取
  3. 文本足够时生成结构化 JSON / OCR 文本 / KB Markdown
  4. 文本不足时标记 `needs_heavy_ocr` / `ocr_model_not_configured`
  5. 仅对有效文本写入上传知识库底座
- 上传知识库采用独立索引目录，不污染现有主 `faiss_db`。
- 新增 OCR 轻量健康探测：
  - `/health/ocr`
  - `/health/dependencies` 中追加 `medicine_ocr`

## 知识库策略

- 优先尝试 FAISS + embedding。
- 若当前环境无法连接 Ollama / 无法完成向量化，则自动降级为 `lexical_corpus`：
  - 仍然写入 `uploaded_pdf_kb/corpus.json`
  - 保留结构化 Markdown 语料
  - 供后续 Agent / 检索逻辑继续接入
- `query_knowledge_base` 已补为“双源查询”：
  - 主知识库
  - 上传 PDF 知识库（FAISS 或 corpus 回退）

## 前端改动

- 上传记录列表新增 OCR / KB 相关状态字段接收。
- 上传页支持单条记录详情拉取。
- 打开上传弹窗后会对 `uploaded / extracting_text / kb processing` 记录轮询刷新状态。
- 结果面板改为展示上传状态、OCR 状态、知识库状态、处理时间和结构化摘要。
- 扫描件倾向 PDF 会明确提示“当前未启用重型 OCR 模型”或“需要后续显式触发重型 OCR”。

## 验证

- 前端生产构建通过：`vite build`
- 定向 pytest 通过：
  - `tests/test_medicine_ocr_runtime.py`
  - `tests/test_admin_pdf_pipeline.py`
  - `tests/test_kb_tools.py`
- 本地 HTTP 联调（`LOCAL_DEV_MODE=true`）已确认：
  - 管理员登录成功
  - 上传最小样本 `数据库简介.pdf` 成功
  - `ocr_status = text_extracted`
  - `kb_ingest_status = succeeded`
  - `kb_index_mode = lexical_corpus`
  - 结构化结果可读
  - 删除链路成功
- 轻量 provider 定向测试已补充：
  - 文本型 PDF 可通过 `pypdf_text` 成功提取并入知识库
  - 空文本 / 扫描倾向 PDF 会落为 `needs_heavy_ocr` 或 `ocr_model_not_configured`
  - 无模型目录时 OCR 健康探测不会抛异常

## 当前阻塞

- 真实模式启动仍被现网 MySQL 连接阻塞：
  - `Can't connect to MySQL server on '10.108.12.164'`
- 因此本轮“真实 HTTP”验证采用 `LOCAL_DEV_MODE=true` 完成上传 / OCR / 知识库链路。
- `medicineOCR` 原始 DeepSeek OCR 路径仍待后续补齐：
  - 本地模型目录
  - PDF 分页渲染
  - 可选 CPU / CUDA 策略
  - 依赖安装与运行时路径
