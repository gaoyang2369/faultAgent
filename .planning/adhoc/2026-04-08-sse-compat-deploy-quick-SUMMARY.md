# SSE 兼容 / 部署收口总结

## 已完成

- 保留并延续上一轮安全修复，不回退：
  - 前端无动态求值
  - SSE 单活连接与显式关闭
  - 后端错误脱敏
  - 服务端不再直接信任前端 `thread_id` / `chat_id`
- 新增 `session_scope.py` 的 legacy 兼容层：
  - 识别旧格式 `thread_id`
  - 通过签名 `fd_legacy_threads` cookie 为当前浏览器会话维护 legacy → signed thread 映射
  - legacy 映射只用于当前会话后续访问，不恢复任意 legacy thread 的直接读取能力
- `app.py` 收口部署行为：
  - 生产环境缺失 `SESSION_SECRET` 时拒绝启动
  - 生产环境缺失 `FRONTEND_ORIGINS` 时记录明确告警
  - `/ai/history/*`、`/api/todos/*` 支持在当前会话内解析已绑定的 legacy alias
- 前端新增本地兼容层：
  - `chatSessionCache.ts` 用 `localStorage` 保存同浏览器已看过的聊天与 todos
  - 当服务端不能安全恢复旧历史时，前端退化为“本地缓存只读”
  - 用户从本地缓存继续提问时，会在 UI 中提示“将切换到新的受控会话”
  - 接管成功后，前端会将 legacy 会话缓存重命名到新的 signed thread
- 清理协议残留：
  - 删除前端 `sendServiceMessage()` 对不存在 `/chat` 旧接口的依赖
  - `ChatSidebar` 增加“本地缓存”标识，避免误判为服务端历史
- 更新部署文档与 `.env.example`：
  - 补充 `APP_ENV`、`SESSION_SECRET`、`FRONTEND_ORIGINS`
  - 补充 `SESSION_COOKIE_SECURE` / `SESSION_COOKIE_SAMESITE` / `SESSION_COOKIE_DOMAIN` / `SESSION_COOKIE_PATH`
  - 明确开发/生产默认值与缺省风险

## 验证

- 后端定向测试：
  - `C:\miniconda3\envs\faultagent312\python.exe -m pytest tests\test_sse_stream.py tests\test_history_api.py tests\test_config.py tests\test_tool_calls.py tests\test_smoke.py -q`
  - 结果：`52 passed`
- 后端全量测试：
  - `C:\miniconda3\envs\faultagent312\python.exe -m pytest tests -q`
  - 结果：`92 passed`
- 前端构建：
  - `npm run build`
  - 结果：通过（沙箱内 `esbuild` 子进程被拦截，已提权重跑成功）

## 兼容边界

- 已解决：
  - 同浏览器内，已看过的旧历史不再“完全消失”，会以前端本地缓存只读形式保留
  - 同浏览器从旧历史继续提问时，可平滑迁移到新的受控 thread
  - 生产部署时不再静默落入“临时 SESSION_SECRET”模式
- 仅缓解、未彻底解决：
  - 未接入真实用户体系前，无法安全地把数据库里所有 legacy thread 自动认领给当前浏览器
  - 本地缓存只覆盖“当前浏览器已经看过”的历史，不覆盖跨浏览器 / 跨设备
- 后续仍需真实用户体系的事项：
  - 用户级 thread ownership
  - 跨设备历史共享
  - 更强的审计与账号级隔离
