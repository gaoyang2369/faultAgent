# SSE / 会话链路高优先级修复总结

## 已完成

- 新增 `session_scope.py`，实现签名 session cookie 与签名 `thread_id`，服务端不再直接信任前端传入的 `thread_id` / `chat_id`
- 收紧 `app.py` 路由作用域：
  - `/chat/stream` 仅复用当前服务端会话拥有的 `thread_id`
  - `/ai/history/*` / `/api/todos/*` 仅返回当前服务端会话拥有的对话数据
  - CORS 从通配改为显式来源列表
- 收紧 `streaming.py` 错误输出：
  - 前端仅收到通用错误消息与 `error_id`
  - traceback 仅保留在服务端日志
  - 心跳改为等待式实现，避免超时取消底层异步生成器
- 重写前端 `agent_fronted/src/services/api.js`：
  - 单活 `EventSource`
  - 新请求前关闭旧连接
  - 网络错误时立即关闭，禁止危险自动重连
  - 补齐 `start` / `ping` / `tool_*` / `complete` / `server_error` 事件消费
- 重构 `useChatStream.ts`：
  - 显式管理 active stream 生命周期
  - 防 stale callback
  - 修复工具提示不显示
  - 补齐加载中 / 流式中 / 工具中 / 完成 / 失败 / 中断状态
- 删除前端动态求值：
  - `useTodosPanel.ts` 移除 `new Function`
- 收紧消息渲染面：
  - `ChatMessage.vue` 移除 `onclick` 注入
  - DOMPurify 不再放行危险事件属性
  - 工具事件改为安全 DOM 渲染，不再依赖 HTML 内联事件
- 移除 `CustomerService.vue` 中无服务端支持的 WebSocket 占位链路
- 新增 / 更新后端测试，覆盖：
  - server_error 不暴露 traceback
  - foreign thread_id / chat_id 无法直接读取历史与 todos
  - 跨会话传入 foreign thread_id 会被服务端替换

## 验证

- `C:\miniconda3\envs\faultagent312\python.exe -m pytest tests\test_sse_stream.py tests\test_tool_calls.py tests\test_smoke.py tests\test_history_api.py tests\test_dcma_boundaries.py -q`
  - 结果：`30 passed`
- `C:\miniconda3\envs\faultagent312\python.exe -m pytest tests -q`
  - 结果：`83 passed, 1 failed`
  - 失败项：`tests/test_config.py::TestConfigDefaults::test_dcma_db_name_default`
  - 原因：当前环境中的 `.env` / 现有配置把 `DCMA_DB_NAME` 设为 `real_data`，与默认值断言冲突；非本轮改动引入
- `npm run build`
  - 结果：通过
- 代码检索：
  - `agent_fronted/src` 中 `new Function` / `eval(` / `onclick=` / `ws://localhost:3000` 均已清除

## 剩余风险

- 当前最小会话隔离依赖签名 cookie 与签名 `thread_id`，尚未接入真实用户体系；跨浏览器 / 跨设备不会共享历史
- `SESSION_SECRET` 未配置时会在进程启动时生成临时密钥，重启后旧会话 cookie 与旧 thread_id 将失效
- SSE 仍使用 GET + EventSource；本轮已禁用危险自动重连，但未升级为 `POST create run + GET stream`
- `sendServiceMessage()` 指向的 `/chat` 入口仍不存在，但当前主流程未使用该接口
