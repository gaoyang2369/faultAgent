# 2026-04-17 后端可观测性与日志链路排障总结

## 结论摘要
- 已确认确实存在“前端动作已打到后端，但应用层缺少对应摘要日志”的问题。
- 根因主要在后端应用层：
  - `/ai/history/*`、`/api/todos/*` 过去几乎只有失败日志，没有成功摘要日志。
  - `/chat/stream` 路由只有零散 thread 相关日志，没有统一入口日志。
  - `streaming.py` 虽已有首 token / complete / error，但缺少 start、tool_start、tool_end、duration 等关键观察点。
  - `LOCAL_DEV_MODE` 下流式链路直接走 `dev_mode.py`，此前完全绕过了 `streaming.py` 的流式生命周期日志。

## 本轮修复
1. 新增 request_id 绑定能力，确保路由日志与 SSE / dev mode 日志能串起来。
2. 新增日志摘要工具，统一压缩 session/thread/user input/tool payload，避免打印完整敏感值。
3. 在 `app.py` 为以下路径补齐入口/成功/拒绝/失败日志：
   - `/chat/stream`
   - `/ai/history/{type}`
   - `/ai/history/{type}/{chat_id}`
   - `/api/todos/{thread_id}`
   - `/health/dependencies`
   - `/health/real`
4. 在 `streaming.py` 补齐：
   - 流式会话开始
   - 首个模型事件
   - 首个有效 token
   - tool_start / tool_end
   - complete / cancel / error 耗时摘要
5. 在 `dev_mode.py` 补齐本地开发模式下的流式/tool/todo 完成日志，避免 dev mode 成为可观测性盲区。

## 真实验证
- 已确认的异常路径：
  - 真实模式启动时，MySQL `10.108.12.164:3306` 当前不可达，后端会输出结构化启动失败日志；`Test-NetConnection` 结果为 `TcpTestSucceeded=False`。
- 已确认的联调验证（`LOCAL_DEV_MODE=true`）：
  - `/health/real?deep=false`
  - `/health/dependencies?deep=false`
  - `/ai/history/service` 初始空列表
  - `/chat/stream` 一次带 tool/todos 的本地开发模式流式请求
  - `/ai/history/service` 刷新后列表
  - `/ai/history/service/{thread}` 历史详情
  - `/api/todos/{thread}` 任务清单
  - `/api/todos/thread.fake.unauthorized` 未授权 warning
- 验证结果：
  - 以上请求现在都能在后端看到对应应用层 JSON 日志。
  - 流式请求与其 dev mode 工具日志共享同一个 request_id。
  - 日志只保留摘要：session/thread 为截断值，用户输入与工具输入/输出为短摘要，无敏感 key/cookie 明文。

## 残留风险
- 真实模式下 `streaming.py` 的 tool_start/tool_end 新日志，本轮由于 MySQL 外部不可达未能做端到端复测；代码路径已补齐，属于高概率正常，但仍建议待真实依赖恢复后再做一轮实链路复测。
- 当前验证输出里的中文在 PowerShell 控制台中出现乱码属于终端编码显示问题，不是接口/日志字段本身的 UTF-8 设计问题。

## 补充验证（同日追加）
- 已追加完成一次真实模式复测：
  - `MySQL 异步连接池初始化成功`
  - `PostgreSQL 数据库表结构初始化成功`
  - `Agent 初始化成功`
  - 真实 `/chat/stream` 工具链返回 `tool_start_events=5`、`tool_end_events=5`、`complete_events=1`
  - `streaming.py` 日志中已确认出现：
    - `流式会话开始`
    - `收到首个模型事件`
    - `收到首个有效 token`
    - 多条 `工具开始`
    - 多条带 `duration_ms` 的 `工具完成`
    - `流式请求完成`
- 因此，“真实 Agent 路径下 tool_start/tool_end/duration 日志是否完整”这一点已从高概率提升为已确认。
