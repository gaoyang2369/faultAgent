# SSE 事件契约

本文记录 `/chat/stream` 与 `/chat/stream/edit` 当前对前端暴露的 SSE 协议。后续重构可以改变内部 agent/workflow 实现，但必须继续输出兼容的事件名和 payload 字段。

## 基本格式

每个事件帧使用标准 SSE 格式：

```text
event: <event_name>
data: <json>

```

要求：

- `data` 必须是 JSON 对象。
- payload 必须有 `type` 字段，前端和测试都会使用它识别语义事件。
- `trace_id` 应贯穿一次请求的 start、tool、complete、error 等事件。
- 不向 `token` 或其他用户可见字段泄露 SQL 草稿、内部 tool JSON、模型思维链或 traceback。
- 断流重连由前端手动触发，后端不应默认重放有副作用的工具调用。

## 前端监听的事件

前端 `agent_fronted/src/services/api.js` 当前显式监听：

- `start`
- `ping`
- `token`
- `tool_start`
- `tool_end`
- `complete`
- `server_error`

后端还会输出 `tool_progress`、`tool_stream` 作为结构化扩展事件。当前前端可以忽略，但测试和外部消费者可能使用。

## `start`

表示后端已经接受请求，并完成 thread 解析。

```json
{
  "type": "chat_start",
  "thread_id": "thread.xxx",
  "stream_id": "stream-id",
  "trace_id": "trace_xxx",
  "stage": "reasoning",
  "message": "模型已开始推理，等待首个可显示 token..."
}
```

字段要求：

- `thread_id` 必须是后端最终采用的 thread。若前端传入 legacy 或越权 thread，这里返回新的或映射后的 thread。
- `stream_id` 应回显前端传入值；未传时由后端生成。
- `stage` 常见值：`reasoning`、`workflow`。

## `ping`

用于长耗时阶段保持连接和更新前端状态。

```json
{
  "type": "ping",
  "timestamp": "2026-06-09T10:00:00",
  "trace_id": "trace_xxx",
  "stage": "reasoning",
  "message": "模型仍在推理，尚未产出可显示内容..."
}
```

字段要求：

- `stage` 常见值：`reasoning`、`tool_call`、`connecting`。
- `message` 是用户可见短状态，不应包含内部错误细节。

## `token`

表示一段可直接展示给用户的 assistant 文本。

```json
{
  "type": "token",
  "content": "本次诊断结果..."
}
```

字段要求：

- `content` 必须是用户可见文本片段。
- legacy ReAct 路径可能多次发送 token；workflow 路径可能只在最终阶段发送一次完整文本。
- 如果上游没有流出可见 token，后端可以在 complete 前补发一次最终文本 token。

## `tool_start`

表示工具调用开始。

```json
{
  "type": "tool_start",
  "tool": "sql_db_query",
  "input": {
    "query": "SELECT ..."
  },
  "run_id": "tool-run-id",
  "trace_id": "trace_xxx",
  "stage": "collect",
  "current_stage": "collect"
}
```

字段要求：

- `tool` 使用前端可识别的工具名，例如 `sql_db_query`、`query_knowledge_base`、`save_report`、`fig_inter`。
- `input` 必须经过 JSON 安全化处理，不应包含密钥、数据库密码或过长原始内容。
- `stage` 用于 workflow 进度，常见值：`collect`、`retrieve`、`analyze`、`report`。

## `tool_end`

表示工具调用完成。

```json
{
  "type": "tool_end",
  "tool": "sql_db_query",
  "result": "完整或结构化结果",
  "result_preview": "前端展示摘要",
  "truncated": false,
  "run_id": "tool-run-id",
  "trace_id": "trace_xxx",
  "stage": "collect",
  "current_stage": "collect",
  "stage_duration_ms": 123.4,
  "evidence": [],
  "evidence_count": 0,
  "evidence_ids": [],
  "action_guard": null
}
```

字段要求：

- `result` 或 `result_preview` 至少存在一个。
- `result_preview` 面向前端展示，应该短且脱敏。
- `truncated` 表示结果是否被截断。
- `evidence`、`evidence_count`、`evidence_ids` 用于证据面板和治理逻辑。
- `action_guard` 用于报告、工单等有风险动作的人工复核提示。

## `tool_progress`

结构化工具进度扩展事件。

```json
{
  "type": "tool_progress",
  "event_type": "tool_progress",
  "trace_id": "trace_xxx",
  "run_id": "tool-run-id",
  "tool_name": "sql_db_query",
  "stage": "collect",
  "message": "工具已开始执行",
  "progress": 0.0,
  "metadata": {
    "current_stage": "collect"
  }
}
```

字段要求：

- `progress` 取值范围为 `0.0` 到 `1.0`。
- 该事件不能替代 `tool_start` / `tool_end`，只能作为扩展。

## `tool_stream`

结构化工具输出流扩展事件。

```json
{
  "type": "tool_stream",
  "event_type": "tool_stream",
  "trace_id": "trace_xxx",
  "run_id": "tool-run-id",
  "tool_name": "sql_db_query",
  "chunk": "工具输出片段",
  "done": true,
  "metadata": {
    "stage": "collect"
  }
}
```

字段要求：

- `chunk` 应脱敏并控制长度。
- `done=true` 表示该工具输出流结束。

## `complete`

表示一次 assistant 生成结束。payload 的 `type` 必须是 `chat_complete`。

### 最小兼容字段

```json
{
  "type": "chat_complete",
  "thread_id": "thread.xxx",
  "trace_id": "trace_xxx",
  "final_content": "最终答复",
  "todos": [],
  "event_count": 5,
  "timestamp": "2026-06-09T10:00:00"
}
```

### 当前增强字段

legacy ReAct 路径和 workflow 适配层会补充以下字段，前端已经会读取其中多项：

```json
{
  "raw_final_content": "未经过证据门禁改写的原始最终内容",
  "grounded_final_content": "带证据标注或门禁处理后的最终内容",
  "evidence_count": 2,
  "evidences": [],
  "normalized_evidences": [],
  "findings": [],
  "finding_links": [],
  "evidence_quality": {
    "gate": "pass",
    "release_ready": true,
    "coverage_summary": {}
  },
  "governance": {},
  "evidence_coverage": {},
  "report_gate": "pass",
  "quality_gate_notice": null,
  "release_ready": true,
  "workflow_stages": ["collect", "retrieve", "analyze", "report"],
  "current_stage": "report",
  "workflow_stage_details": [],
  "tool_lifecycle_ledger": [],
  "route_result": {},
  "planning": {},
  "workflow_result": {},
  "workflow_envelope": {},
  "scenario_result": {},
  "artifacts": [],
  "timeline": [],
  "report_filename": "report.md",
  "report_url": "/reports/report.md",
  "report_artifact": {}
}
```

字段要求：

- `final_content` 是前端最终展示 fallback；如果存在 `grounded_final_content`，前端优先展示 `grounded_final_content`。
- `raw_final_content` 用于保留模型原始结论，不能被质量门禁覆盖。
- `report_gate` 常见值：`pass`、`review_required`、`blocked`。
- `release_ready` 必须是布尔值或 null。
- `workflow_stage_details` 中每项至少包含 `stage`、`status`、`tool_count`；已有代码还会使用 `duration_ms`、`evidence_ids`。
- `tool_lifecycle_ledger` 用于展示工具生命周期与证据/结论关联，不能包含敏感凭据。

### 取消场景

用户停止生成时，workflow runner 可以输出：

```json
{
  "type": "chat_complete",
  "thread_id": "thread.xxx",
  "cancelled": true,
  "cancel_reason": "user_stop",
  "final_content": "",
  "todos": [],
  "timestamp": "2026-06-09T10:00:00"
}
```

## `server_error`

表示服务端处理失败。事件名是 `server_error`，payload `type` 为 `error`。

```json
{
  "type": "error",
  "message": "请求处理失败，请稍后重试",
  "error_id": "request-id",
  "trace_id": "trace_xxx",
  "error": {
    "code": "INTERNAL_ERROR",
    "message": "请求处理失败，请稍后重试",
    "retryable": false,
    "details": {
      "category": "internal"
    },
    "trace_id": "trace_xxx",
    "run_id": null
  }
}
```

字段要求：

- 不能返回 traceback、原始 SQL 密码、API key、完整环境变量或内部连接串。
- 模型网关错误应映射为稳定 `code`，例如鉴权、额度、上游不可用。
- `retryable` 只在模型流中断、知识库暂不可用等可重试场景为 true。

## 重构检查清单

- 所有业务路径都必须通过统一 SSE adapter 输出上述事件。
- Workflow 主链路和 legacy ReAct fallback 的 `complete` 字段应尽量对齐。
- 单元测试应验证事件名序列、字段存在性、错误脱敏、取消场景和 workflow 增强字段。
- 新增事件必须向后兼容；不能删除前端当前监听的事件。
