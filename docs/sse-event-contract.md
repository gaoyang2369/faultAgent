# SSE 事件契约

本文记录 `/chat/stream` 与 `/chat/stream/edit` 当前对前端暴露的 SSE 协议。后端内部已经收敛为限制型单 Agent，但 payload 仍保留部分 `workflow_*` 字段名，作为前端兼容字段。

## 基本格式

```text
event: <event_name>
data: <json>

```

要求：

- `data` 必须是 JSON 对象。
- payload 必须有 `type` 字段。
- `trace_id` 应贯穿 start、ping、tool、complete、server_error。
- 用户可见字段不能泄露 SQL 草稿、密钥、traceback 或模型思维链。

## 事件序列

真实模式常见序列：

```text
start -> ping* -> tool_start/tool_end* -> token -> complete
```

取消或异常时：

```text
start -> ... -> complete(cancelled=true)
start -> ... -> server_error
```

## start

```json
{
  "type": "chat_start",
  "thread_id": "thread.xxx",
  "stream_id": "stream-id",
  "trace_id": "trace_xxx",
  "stage": "understand",
  "message": "限制型单 Agent 已开始处理请求。"
}
```

`thread_id` 必须是后端最终采用的 thread；未授权或旧式 thread 会被重新签发或映射。

## ping

长耗时阶段的保活事件。

```json
{
  "type": "ping",
  "trace_id": "trace_xxx",
  "stage": "analysis",
  "message": "单 Agent 正在处理，连接保持中..."
}
```

常见 `stage`：`understand`、`sql`、`knowledge`、`analysis`、`report`、`final_answer`。

## token

用户可见 assistant 文本。

```json
{
  "type": "token",
  "content": "本次诊断结果..."
}
```

当前 runner 通常在最终阶段发送一次完整文本。

## tool_start / tool_end

当前工具白名单：

- `sql_db_query_checker`
- `sql_db_query`
- `query_knowledge_base`
- `save_report`

```json
{
  "type": "tool_start",
  "tool": "sql_db_query",
  "input": { "query": "SELECT ..." },
  "run_id": "sql_db_query-1",
  "trace_id": "trace_xxx",
  "stage": "sql",
  "current_stage": "sql"
}
```

```json
{
  "type": "tool_end",
  "tool": "sql_db_query",
  "result_preview": "查询结果摘要",
  "truncated": false,
  "run_id": "sql_db_query-1",
  "trace_id": "trace_xxx",
  "stage": "sql",
  "current_stage": "sql",
  "stage_duration_ms": 123.4
}
```

`input`、`result` 和 `result_preview` 必须脱敏，并在过长时截断。

## complete

```json
{
  "type": "chat_complete",
  "thread_id": "thread.xxx",
  "trace_id": "trace_xxx",
  "runtime": "restricted_single_agent",
  "final_content": "最终答复",
  "report_filename": "report.md",
  "report_url": "/reports/report.md",
  "decision": {},
  "sql_artifact": {},
  "knowledge_artifact": {},
  "analysis_artifact": {},
  "report_artifact": {},
  "artifact": {},
  "trace": {},
  "todos": [],
  "event_count": 5,
  "timestamp": "2026-06-09T10:00:00"
}
```

为兼容现有前端，complete 还会尽量补充：

- `workflow_result`
- `workflow_envelope`
- `scenario_result`
- `artifacts`
- `timeline`
- `governance`
- `evidences`
- `normalized_evidences`
- `evidence_count`
- `findings`
- `finding_links`

这些字段名属于前端兼容契约，不代表后端仍有独立 workflow runner。

取消时：

```json
{
  "type": "chat_complete",
  "thread_id": "thread.xxx",
  "trace_id": "trace_xxx",
  "cancelled": true,
  "cancel_reason": "user_stop",
  "final_content": "",
  "todos": [],
  "timestamp": "2026-06-09T10:00:00"
}
```

## server_error

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
    "details": { "category": "single_agent" },
    "trace_id": "trace_xxx",
    "run_id": null
  }
}
```

错误事件不能返回 traceback、原始连接串、API key、数据库密码或完整环境变量。
