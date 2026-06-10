---
mode: execute-phase
date: 2026-06-09
task: backend-service-layer-phase2
---

# 后端应用服务层 Phase2 总结

## 完成内容

- 新增 `fault_diagnosis/services/` 应用服务层。
- 抽取 `TtsService`：负责 `/tts/synthesize` 的文本读取、长度校验、TTS 请求和错误映射；`api/tts.py` 保留旧测试 patch 点。
- 抽取 `GovernanceService`：负责治理快照文件落盘、快照列表分组、治理台账创建/查询/更新。
- 抽取 `AdminPdfService`：负责管理员 PDF 上传登记、处理调度、归档调度、人工校正和删除。
- 抽取 `HistoryService`：负责历史列表、分页、详情读取、删除、Todo 汇总、dev mode 与 checkpointer 差异。
- 抽取 `ChatService`：负责主聊天 SSE 建流、编辑重生成、`/agent/chat` 非流式聚合、`/chat/stop` 停止流。
- 新增 `tests/test_backend_services.py`，直接覆盖 TTS、Governance、History 服务层核心路径。
- 更新 `docs/backend-refactor-roadmap.md`，记录 Phase 2 已完成并把下一步调整为 Phase 3 SSE/事件模型拆分。

## 兼容性

- HTTP 路径、请求参数和响应字段保持不变。
- `fault_diagnosis.api.chat.token_stream_events`、`fault_diagnosis.api.tts._request_tts_audio`、`fault_diagnosis.api.tts.TTS_SYNTHESIZE_URL`、`fault_diagnosis.api.governance.REPORTS_DIR` 等现有测试 patch 点保留。
- `fault_diagnosis.app:app` 入口未改变。

## 验证

- 通过：`python -m compileall fault_diagnosis\app.py fault_diagnosis\api fault_diagnosis\services`
- 通过：`python -m compileall fault_diagnosis\app.py fault_diagnosis\api fault_diagnosis\services tests\test_backend_services.py tests\test_tts_synthesize_api.py tests\test_governance_api.py tests\test_history_api.py tests\test_agent_chat_api.py tests\test_chat_edit_api.py tests\test_admin_pdf_pipeline.py`
- 通过：`git diff --check`，仅输出 Windows 换行提示。
- 阻塞：`python -c "from fault_diagnosis.services.tts_service import TtsService ..."`，当前默认 Python 3.14 环境缺少 `fastapi`。
- 阻塞：`python -m pytest tests\test_backend_services.py tests\test_tts_synthesize_api.py tests\test_governance_api.py tests\test_history_api.py tests\test_agent_chat_api.py tests\test_chat_edit_api.py tests\test_admin_pdf_pipeline.py -q`，当前默认 Python 3.14 环境缺少 `pytest`。
- 阻塞：当前 shell 未找到 `conda` 或 `pytest`，只能在可用的 `faultagent` Python 3.12 环境中补跑 import 和 pytest。

## 后续建议

- 在 `faultagent` 环境补跑 Phase 1/2 定向测试和 smoke 测试。
- Phase 3 优先拆出 `agent_runtime/event_contracts.py` 与 `agent_runtime/sse_adapter.py`，把 `streaming.py` 的 SSE 帧拼装和运行时业务执行继续解耦。
