# 后端 API 契约

本文记录当前后端已经暴露、且前端或外部语音网关已经依赖的 HTTP API。后续重构可以移动实现位置，但不应在没有迁移方案的情况下改变路径、请求参数、响应外壳或 cookie 行为。

## 兼容原则

- 文中路径是 FastAPI 后端原始路径。前端开发环境通过 Vite 将 `/api/*` 转发到后端并移除 `/api` 前缀。
- 前端所有业务请求默认携带 cookie，后端必须继续维护 `fd_session`、`fd_legacy_threads` 和 `fd_admin_auth` 的兼容行为。
- `user_identity` 查询参数只用于兼容前端传参，不是权限边界；真实身份由服务端 session/admin cookie 派生。
- `/reports` 与 `/images` 是前端直接使用的静态资源路径，报告生成和图像生成必须继续写入可被这些路径访问的位置。
- 没有分页参数时，旧历史接口仍返回 `thread_id[]`；带分页参数时返回分页对象。

## 聊天与 Agent

| 方法 | 路径 | 用途 | 请求 | 响应 |
| --- | --- | --- | --- | --- |
| `GET` | `/chat/stream` | 主聊天 SSE 流 | query: `message` 必填；`thread_id`、`user_identity`、`stream_id` 可选 | `text/event-stream`，见 [sse-event-contract.md](./sse-event-contract.md) |
| `GET` | `/chat/stream/edit` | 编辑某个用户轮次后重新生成 | query: `message`、`thread_id`、`user_turn_index` 必填；`user_identity`、`stream_id` 可选 | `text/event-stream`，事件外壳与 `/chat/stream` 一致 |
| `POST` | `/chat/stop` | 停止当前会话中的活跃流 | JSON: `stream_id` 必填，`reason` 默认 `user_stop` | `{ ok, status, stream_id?, thread_id?, cancel_reason? }` |
| `POST` | `/agent/chat` | 语音网关兼容的非流式 Agent 接口 | JSON: `{ message, session_id?, metadata? }` | `{ reply_text, visual_actions, session_id, thread_id, metadata? }`；失败时可返回 502 JSON |

### 聊天契约要点

- `/chat/stream` 可以接收旧式 legacy `thread_id`，后端会签发或映射为服务端签名 thread，并通过 cookie 保存映射。
- 未授权访问其他会话 thread 时，后端必须拒绝复用并签发新 thread，不能泄露历史。
- `/chat/stream/edit` 需要根据 `user_turn_index` 截断历史，只保留目标用户消息之前的上下文，并清理 stale artifact。
- `/agent/chat` 内部消费 SSE 事件，聚合 `token` / `complete` / `tool_end` 成 JSON，不应复制一套独立 agent 逻辑。

## 历史与 Todo

| 方法 | 路径 | 用途 | 请求 | 响应 |
| --- | --- | --- | --- | --- |
| `GET` | `/ai/history/{type}` | 当前会话历史列表 | path: `type`；query: `limit`、`cursor`、`q` 可选 | 无分页参数时返回 `string[]`；带分页参数时返回 `{ items, has_more, next_cursor, limit, cursor, keyword, total_returned }` |
| `GET` | `/ai/history/{type}/{chat_id}` | 获取单个会话消息 | path: `type`, `chat_id` | 消息数组；无权限或找不到时返回 `[]` |
| `DELETE` | `/ai/history/{type}/{chat_id}` | 删除当前会话拥有的历史 | path: `type`, `chat_id` | `{ deleted: true, server_deleted: true, thread_id }` |
| `GET` | `/api/todos/{thread_id}` | 获取某会话 Todo | path: `thread_id`；query: `status` 可选 | `{ thread_id, todos, summary: { total, pending, in_progress, completed } }` |

### 历史契约要点

- 历史和 Todo 都必须按当前 `fd_session` 做归属过滤。
- 分页兼容模式不能破坏旧前端或脚本对 `string[]` 的期待。
- `type` 当前主要被前端传 `service`，也兼容 `chat`、`pdf` 等历史类型标签。

## 身份与权限

| 方法 | 路径 | 用途 | 请求 | 响应 |
| --- | --- | --- | --- | --- |
| `GET` | `/auth/identity` | 查询当前身份 | cookie | `{ user_id, user_role, is_admin, auth_method, available_auth_methods }` |
| `POST` | `/auth/admin/login` | 管理员密码登录 | JSON: `{ username, password }` | 成功返回管理员身份并写入 admin cookie；失败返回 401 |
| `POST` | `/auth/logout` | 退出管理员态 | cookie | 游客身份，并清理 admin cookie |

### 身份契约要点

- 管理员权限以服务端签名 cookie 为准，不能信任前端传来的身份字段。
- 管理员 PDF 相关接口必须继续要求 `is_admin=true`。

## 管理员 PDF

| 方法 | 路径 | 用途 | 请求 | 响应 |
| --- | --- | --- | --- | --- |
| `GET` | `/admin/pdfs` | 列出 PDF 上传记录 | admin cookie | `{ records: [...] }` |
| `POST` | `/admin/pdfs` | 上传 PDF 并登记 | multipart: `file` | `{ record, duplicate }`，新文件状态码 201，重复文件 200 |
| `GET` | `/admin/pdfs/{record_id}` | 获取单条记录详情 | admin cookie | 单条 record 对象 |
| `GET` | `/admin/pdfs/{record_id}/file` | 内联读取原 PDF | admin cookie | `application/pdf` 文件响应 |
| `POST` | `/admin/pdfs/{record_id}/ingest` | 手动归档到上传知识库 | admin cookie | `{ record, scheduled, already_ingested, message }` |
| `PATCH` | `/admin/pdfs/{record_id}/correction` | 保存人工校正文本 | JSON: `corrected_text` 或 `correction_text` | `{ record, message, next_action }` |
| `DELETE` | `/admin/pdfs/{record_id}` | 删除记录及文件 | admin cookie | `{ deleted: true, record_id }` |

### PDF record 前端依赖字段

前端会同时兼容 snake_case 和 camelCase。后端应优先保持 snake_case 字段，例如：

- 基础：`id`、`file_name`、`file_size`、`file_type`、`uploaded_at`
- OCR：`ocr_status`、`ocr_error`、`ocr_backend`、`result_preview`、`structured_result`
- 知识库：`kb_ingest_status`、`kb_error`、`kb_document_id`、`kb_index_mode`
- Agent 可用性：`agent_ingest_status`、`agent_query_ready`、`agent_queryable`
- 校正：`has_correction`、`correction_text`、`correction_preview`、`correction_needs_reingest`
- 状态展示：`status_label`、`status_timeline`、`next_action`
- 文件访问：`file_url`

## 治理快照与台账

| 方法 | 路径 | 用途 | 请求 | 响应 |
| --- | --- | --- | --- | --- |
| `POST` | `/api/governance/save` | 保存治理快照文件 | JSON: `{ markdown, json_content, doc_template, report_markdown?, backlog_markdown?, thread_id? }` | `{ ok, thread_id, markdown_path, json_path, doc_template_path, report_path?, backlog_path? }` |
| `GET` | `/api/governance/list` | 列出治理快照 | query: `thread_id?`, `limit?` | `{ items, thread_id, limit }` |
| `POST` | `/api/governance/ledger` | 创建治理台账记录 | JSON: ledger payload | `{ ok, record_id, ...index_entry }` |
| `GET` | `/api/governance/ledger` | 查询治理台账 | query: `thread_id?`, `limit?`, `status?`, `priority?`, `owner?`, `tag?` | `{ items, summary, thread_id, limit, filters }` |
| `POST` | `/api/governance/ledger/update` | 更新台账状态 | JSON: `{ record_id, status?, owner?, next_action?, verified_result?, due_date?, priority?, tags? }` | `{ ok, record_id, ...index_entry }` |

## TTS 与健康检查

| 方法 | 路径 | 用途 | 请求 | 响应 |
| --- | --- | --- | --- | --- |
| `POST` | `/tts/synthesize` | 文本转发给 TTS 服务 | JSON 或表单中包含 `text` | `{ audio, sample_rate? }`；TTS 不可用时 502 |
| `GET` | `/health/dependencies` | 依赖健康检查 | query: `deep` 默认 true | `{ status, checks, ... }` |
| `GET` | `/health/ocr` | OCR provider 轻量状态 | 无 | OCR 状态对象 |
| `GET` | `/health/real` | `/health/dependencies` 别名 | query: `deep` 默认 true | 依赖健康检查对象 |
| `GET` | `/` | 根路径信息或静态前端入口前的 API 信息 | 无 | API 信息；静态挂载后同路径也可能服务前端资源 |

## 当前未实现但前端有降级的路径

前端 `documentRecognitionAPI` 会尝试访问以下路径，并在 404 时降级：

- `/admin/recognition/upload`
- `/admin/recognition/ocr`
- `/admin/recognition/restore-image`
- `/admin/recognition/markdown-to-pdf`

后端重构时不要误删前端 fallback。若后续实现这些接口，应单独补充契约。

## 后续迁移检查清单

- 路由拆分后，所有路径、方法和状态码兼容。
- 所有 JSON 响应仍会附带 session scope cookie。
- SSE 事件字段满足 `docs/sse-event-contract.md`。
- `/reports`、`/images` 静态路径仍可访问。
- 历史、Todo、PDF 文件访问继续执行 session/admin 权限过滤。
