# 后端 API 契约

本文记录当前 FastAPI 后端对浏览器前端、语音网关和运维脚本暴露的 HTTP API。实现可以重构，但路径、方法、cookie 行为、权限边界和主要响应外壳不能在没有迁移方案的情况下改变。

当前主入口是限制型单 Agent。所有诊断请求最终进入 `/chat/stream` 主链路；`/agent/chat` 只是语音网关的 JSON 聚合入口。

## 通用约定

- 后端原始路径不带 Vite 代理前缀；前端开发环境可能通过 `/api/*` 转发到后端。
- 浏览器请求默认携带 cookie。真实身份来自服务端签名 session / auth cookie，不信任请求体或 query 中的身份字段。
- `/chat/stream` 的 `user_identity` 只是兼容参数，不参与授权。
- 受保护资源必须返回当前 session scope cookie。
- 报告通过 `GET /reports/{filename}` 受保护访问，不再作为公共静态目录直接暴露。
- 错误响应不要泄露 traceback、数据库连接串、API key、环境变量或模型原始错误细节。

## 聊天与 Agent

| 方法 | 路径 | 用途 | 请求 | 响应 |
| --- | --- | --- | --- | --- |
| `GET` | `/chat/stream` | 主聊天 SSE 流 | query: `message` 必填；`thread_id`、`user_identity`、`stream_id` 可选 | `text/event-stream`，见 [sse-event-contract.md](./sse-event-contract.md) |
| `GET` | `/chat/stream/edit` | 编辑某个用户轮次后重新生成 | query: `message`、`thread_id`、`user_turn_index` 必填；`user_identity`、`stream_id` 可选 | `text/event-stream` |
| `POST` | `/chat/stop` | 停止当前会话中的活跃流 | JSON: `stream_id` 必填，`reason` 默认 `user_stop` | `{ ok, status, stream_id?, thread_id?, cancel_reason? }` |
| `POST` | `/agent/chat` | 语音网关兼容 JSON 入口 | JSON: `{ message, session_id?, metadata? }` | `{ reply_text, visual_actions, session_id, thread_id, metadata? }`；失败时可返回 502 |
| `GET` | `/chat/plan` | goal-native plan 调试快照 | query: `message` 必填；`thread_id`、`user_identity` 可选；仅 `ENABLE_PLAN_ENDPOINT` 或 `LOCAL_DEV_MODE` 启用 | `agent_plan_snapshot.v2` JSON；不改变真实执行 |

契约要点：

- `/chat/stream` 会把请求 thread 解析到当前服务端 session；不属于当前 session 的 thread 不能复用。
- `/chat/stream/edit` 会截断目标用户轮次后的历史，并清理过时 thread artifact。
- `/agent/chat` 内部消费同一条 SSE 流，聚合 `token`、`complete`、`tool_end` 等事件，不复制一套 Agent runner。
- `/chat/plan` 输出 `resolved_context`、`goal_set`、`task_family`、`policy_id`、`enabled_nodes`、`runtime_tools`、`readiness`、`manual_confirmation`、`evidence_gaps` 和 `authorization`。

## 认证与身份

| 方法 | 路径 | 用途 | 请求 | 响应 |
| --- | --- | --- | --- | --- |
| `GET` | `/auth/identity` | 查询当前身份 | cookie | 身份 payload，包含角色、权限、资源范围和 auth method |
| `POST` | `/auth/login` | 文件用户登录 | JSON: `{ username, password }` | 成功写入普通用户 cookie；失败 401 |
| `POST` | `/auth/admin/login` | 管理员登录 | JSON: `{ username, password }` | 成功写入 admin cookie；失败 401 |
| `POST` | `/auth/dev-login` | 本地开发身份登录 | JSON: `{ role, user_id?, asset_scope?, allowed_tables? }`；仅非生产且 dev auth 开启 | 写入 dev auth cookie；未启用返回 404 |
| `POST` | `/auth/voice/exchange` | 语音 HMAC 身份换后端 session | JSON: `{ user, role, timestamp, nonce, signature }` | 成功写入普通用户 cookie；失败 403 |
| `POST` | `/auth/logout` | 退出 | cookie | 清理普通用户、管理员和开发身份 cookie，返回 guest 身份 |

身份要点：

- 服务端 session / cookie 是真实授权来源。
- 语音直连 `/agent/chat` 也可使用 `X-Voice-*` header；浏览器场景优先用 `/auth/voice/exchange` 换 cookie。
- HMAC 签名窗口由 `VOICE_AUTH_MAX_AGE_SECONDS` 控制，密钥为 `VOICE_AUTH_SHARED_SECRET`。

## 报告

| 方法 | 路径 | 用途 | 请求 | 响应 |
| --- | --- | --- | --- | --- |
| `GET` | `/reports/{filename}` | 读取 HTML 报告 | path: 安全文件名，cookie | `text/html` 文件；无权限 403；不存在 404 |

报告要点：

- 只允许读取 `trash/run/reports/` 下匹配安全命名规则的 `.html` 文件。
- 普通用户必须通过同名 `.access.json` 校验设备和表范围。
- 管理员可读取全部报告。

## 历史与 Todo

| 方法 | 路径 | 用途 | 请求 | 响应 |
| --- | --- | --- | --- | --- |
| `GET` | `/ai/history/{type}` | 当前会话历史列表 | path: `type`；query: `limit`、`cursor`、`q` 可选 | 旧模式返回 `string[]`；分页模式返回 `{ items, has_more, next_cursor, limit, cursor, keyword, total_returned }` |
| `GET` | `/ai/history/{type}/{chat_id}` | 获取单个会话消息 | path: `type`, `chat_id` | 消息数组；无权限或找不到返回 `[]` |
| `DELETE` | `/ai/history/{type}/{chat_id}` | 删除当前会话拥有的历史 | path: `type`, `chat_id` | `{ deleted: true, server_deleted: true, thread_id }` |
| `GET` | `/api/todos/{thread_id}` | 获取某会话 Todo | path: `thread_id`；query: `status` 可选 | `{ thread_id, todos, summary }` |

历史和 Todo 必须按当前 session 做归属过滤。

## 工单

| 方法 | 路径 | 用途 | 请求 | 响应 |
| --- | --- | --- | --- | --- |
| `POST` | `/api/workorders` | 创建本地工单记录 | JSON: `CreateWorkOrderPayload` | `{ ok: true, work_order }` |
| `GET` | `/api/workorders` | 列出工单 | query: `thread_id?`, `trace_id?`, `status?`, `limit?` | `{ items, summary }` |
| `GET` | `/api/workorders/{work_order_id}` | 查看工单详情 | path: `work_order_id` | `{ ok: true, work_order }` 或 404 |
| `POST` | `/api/workorders/update` | 更新草稿/待派单信息 | JSON: `UpdateWorkOrderPayload` | `{ ok: true, work_order }` 或 404 |

工单要点：

- Agent 主链路只输出工单建议或草稿边界，不会自动调用创建接口。
- `update` 不允许把状态改成派发或执行类状态；派发和执行需要独立审批系统。
- 工程师只能操作授权设备范围内的工单；管理员可查看全部。

## 管理员 PDF

| 方法 | 路径 | 用途 | 请求 | 响应 |
| --- | --- | --- | --- | --- |
| `GET` | `/admin/pdfs` | 列出 PDF 上传记录 | admin cookie | `{ records: [...] }` |
| `POST` | `/admin/pdfs` | 上传 PDF 并登记 | multipart: `file` | 新文件 201，重复文件 200 |
| `GET` | `/admin/pdfs/{record_id}` | 获取单条记录详情 | admin cookie | record 对象 |
| `GET` | `/admin/pdfs/{record_id}/file` | 内联读取原 PDF | admin cookie | `application/pdf` 文件 |
| `POST` | `/admin/pdfs/{record_id}/ingest` | 归档到上传知识库 | admin cookie | `{ record, scheduled, already_ingested, message }` |
| `PATCH` | `/admin/pdfs/{record_id}/correction` | 保存人工校正文本 | JSON: `corrected_text` 或 `correction_text` | `{ record, message, next_action }` |
| `DELETE` | `/admin/pdfs/{record_id}` | 删除记录及文件 | admin cookie | `{ deleted: true, record_id }` |

## 治理快照与台账

| 方法 | 路径 | 用途 | 请求 | 响应 |
| --- | --- | --- | --- | --- |
| `POST` | `/api/governance/save` | 保存治理快照 | JSON: `{ markdown, json_content, doc_template, report_markdown?, backlog_markdown?, thread_id? }` | `{ ok, thread_id, markdown_path, json_path, doc_template_path, report_path?, backlog_path? }` |
| `GET` | `/api/governance/list` | 列出治理快照 | query: `thread_id?`, `limit?` | `{ items, thread_id, limit }` |
| `POST` | `/api/governance/ledger` | 创建治理台账 | JSON: ledger payload | `{ ok, record_id, ...index_entry }` |
| `GET` | `/api/governance/ledger` | 查询治理台账 | query: `thread_id?`, `limit?`, `status?`, `priority?`, `owner?`, `tag?` | `{ items, summary, thread_id, limit, filters }` |
| `POST` | `/api/governance/ledger/update` | 更新台账 | JSON: `{ record_id, status?, owner?, next_action?, verified_result?, due_date?, priority?, tags? }` | `{ ok, record_id, ...index_entry }` |

## TTS 与健康检查

| 方法 | 路径 | 用途 | 请求 | 响应 |
| --- | --- | --- | --- | --- |
| `POST` | `/tts/synthesize` | 转发文本给 TTS 服务 | JSON `{ text }` 或纯文本 body | `{ audio, sample_rate? }`；TTS 不可用时 502 |
| `GET` | `/health/dependencies` | 依赖健康检查 | query: `deep` 默认 true | `{ status, checks, ... }` |
| `GET` | `/health/real` | 真实环境健康检查别名 | query: `deep` 默认 true | 同 `/health/dependencies` |
| `GET` | `/health/ocr` | OCR provider 轻量状态 | 无 | OCR 状态对象 |
| `GET` | `/` | API 根信息 | 无 | `{ message, streaming_endpoint, features }` |

## 迁移检查清单

- 所有 JSON 响应仍附带 session scope cookie。
- `/chat/stream` 和 `/agent/chat` 仍复用同一条 Agent 主链路。
- `/reports/{filename}` 仍执行报告权限校验。
- 历史、Todo、PDF 文件、工单读取继续执行 session/admin/resource 过滤。
- SSE 事件字段满足 [sse-event-contract.md](./sse-event-contract.md)。
