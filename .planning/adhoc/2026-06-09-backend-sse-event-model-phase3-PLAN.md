# Backend SSE Event Model Phase3 Plan

## 目标

拆分 SSE 协议适配与内部事件模型，让 `streaming.py` 逐步退化为兼容入口，避免继续把业务执行、事件字段补齐、SSE 拼帧和错误脱敏混在同一文件。

## 范围

- 新增 `fault_diagnosis/agent_runtime/event_contracts.py`：定义聊天流内部事件模型。
- 新增 `fault_diagnosis/agent_runtime/sse_adapter.py`：统一负责事件模型到 SSE 帧的编码、trace 注入、错误事件补齐和 Workflow complete 增强。
- 保持 `/chat/stream`、`/chat/stream/edit`、`/agent/chat` 和 `/chat/stop` 外部行为不变。
- 保持现有 SSE 事件名与 payload 字段兼容，不删除前端已监听事件。

## 验收

- 现有 SSE 事件序列仍包含 `start`、`token`、`tool_start`、`tool_end`、`complete`、`server_error`。
- `server_error` 继续输出结构化错误并脱敏。
- Workflow 路径通过统一 adapter 注入 `trace_id` 并补充 Phase4 complete 字段。
- 新增 adapter 单元测试覆盖编码、trace 注入和错误补齐。

## 风险控制

- 本轮不移动 workflow runner 与 legacy ReAct 执行逻辑，只抽取协议层。
- 保留 `fault_diagnosis.streaming` 中既有 helper 名称，避免破坏当前测试 patch 路径。
- 不引入新依赖，不升级 LangChain/LangGraph/FastAPI。
