# SSE 事件契约

本文记录 `/chat/stream` 与 `/chat/stream/edit` 当前对前端暴露的 SSE 协议。后端内部主链路是限制型单 Agent；部分 `workflow_*` 字段名仍作为前端兼容字段保留，不代表存在独立 workflow runner。

## 基本格式

```text
event: <event_name>
data: <json>

```

要求：

- `data` 必须是 JSON 对象。
- payload 必须有 `type` 字段。
- `trace_id` 应贯穿 `start`、`ping`、`tool_*`、`task_update`、`complete`、`server_error`。
- 用户可见字段不能泄露 SQL 草稿以外的敏感信息、密钥、traceback、数据库连接串或模型思维链。

## 事件序列

真实模式常见序列：

```text
start -> task_update* -> ping* -> tool_start/tool_end* -> token -> complete
```

取消或异常：

```text
start -> ... -> complete(cancelled=true)
start -> ... -> server_error
```

轻量问候可能只有：

```text
start -> token -> complete
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

`thread_id` 是后端最终采用的 thread。请求的旧 thread 或越权 thread 会被重新映射或拒绝复用。

## task_update

`task_update` 是前端进度面板事件。它不是完整内部 stage 列表，而是由 `single_agent/workflow/todos.py` 聚合后的少量用户可见阶段。

```json
{
  "type": "task_update",
  "thread_id": "thread.xxx",
  "trace_id": "trace_xxx",
  "current_stage": "sql",
  "todos": [
    {
      "id": "collect_evidence",
      "title": "收集证据",
      "status": "in_progress"
    }
  ],
  "summary": {
    "total": 5,
    "pending": 3,
    "in_progress": 1,
    "completed": 1
  }
}
```

前端应把它当作展示进度，不要反向推断内部 policy 或工具权限。

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

## tool_start / tool_end

当前生产工具白名单：

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
  "stage_duration_ms": 123.4,
  "evidence_count": 2,
  "evidence_ids": ["ev_sql_sample_window"]
}
```

`input`、`result`、`result_preview` 必须脱敏，并在过长时截断。工具事件只说明阶段调用了受限工具，不代表模型自由选择工具。

## token

用户可见 assistant 文本。

```json
{
  "type": "token",
  "content": "本次诊断结果..."
}
```

当前 runner 通常在最终阶段发送一次完整文本。

## complete

完整诊断完成事件示例：

```json
{
  "type": "chat_complete",
  "thread_id": "thread.xxx",
  "trace_id": "trace_xxx",
  "request_id": "request.xxx",
  "runtime": "restricted_single_agent",
  "task_family": "diagnosis",
  "policy_id": "fault_diagnosis_v1",
  "final_content": "最终答复",
  "report_filename": "report.html",
  "report_url": "/reports/report.html",
  "decision": {
    "goal_set": {},
    "enabled_nodes": {},
    "runtime_tools": []
  },
  "resolved_context": {},
  "goal_set": {},
  "readiness": {},
  "manual_confirmation": {},
  "sql_artifact": {},
  "knowledge_artifact": {},
  "analysis_artifact": {},
  "workorder_decision": {},
  "report_artifact": {},
  "evidence_bundle": {},
  "output_guardrail": {},
  "artifact": {},
  "trace": {},
  "todos": [],
  "event_count": 5,
  "timestamp": "2026-07-02T10:00:00"
}
```

推荐前端和调试工具优先消费：

- `resolved_context`
- `goal_set`
- `task_family`
- `policy_id`
- `decision.enabled_nodes`
- `decision.runtime_tools`
- `readiness`
- `manual_confirmation`
- `evidence_bundle`
- `output_guardrail`
- `artifact`
- `trace`

兼容字段可能包括：

- `workflow_route`
- `workflow_policy`
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

这些字段名属于前端兼容契约，不是内部事实来源。旧任务类型和旧意图投影如出现在 `decision` 或 `workflow_route`，也只用于兼容展示和历史 artifact。

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
  "timestamp": "2026-07-02T10:00:00"
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

常见错误分类：

- `MODEL_AUTH_FAILED`
- `MODEL_QUOTA_EXHAUSTED`
- `UPSTREAM_UNAVAILABLE`
- `INTERNAL_ERROR`

错误事件不能返回 traceback、原始连接串、API key、数据库密码或完整环境变量。
