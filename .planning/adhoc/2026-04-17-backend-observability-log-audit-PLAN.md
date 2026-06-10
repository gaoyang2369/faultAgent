# 2026-04-17 后端可观测性与日志链路排障计划

## 背景
- 用户观察到“前端有新动作/状态变化，但后端没有相应日志”。
- 目标不是盲目加日志，而是先建立“前端动作 ↔ 后端请求/事件 ↔ 后端日志”的映射，再对已确认盲区做最小侵入修复。

## 本轮范围
1. 审计前端会触发后端的主要路径：
   - 普通聊天发送 `/chat/stream`
   - history 列表与详情 `/ai/history/...`
   - todos `/api/todos/{thread}`
   - 页面初始化/刷新恢复
   - 健康检查 `/health/real`、`/health/dependencies`
2. 检查后端当前日志覆盖：
   - FastAPI 路由入口/成功/失败
   - SSE 生命周期
   - tool_start / tool_end
   - 启动、关闭、依赖初始化
3. 对已确认缺口做最小侵入修复，不改 SSE 协议、不改业务语义。
4. 真实联调验证“前端会打到后端的动作”是否都能看到足够但不过量的日志。

## 当前初步判断
- 已确认：
  - `/ai/history/*` 与 `/api/todos/*` 目前基本只有失败日志，没有成功摘要日志。
  - `/chat/stream` 路由缺少稳定的请求入口日志，只有“签发新 thread”这类局部日志。
  - `streaming.py` 已有首 token / complete / error，但缺少 stream start、tool_start/tool_end 摘要和耗时信息。
- 高概率：
  - 健康检查接口缺少应用层摘要日志，目前主要依赖 uvicorn access log。
  - 现有 uvicorn access log 与应用 JSON 日志分裂，导致 request_id / thread_id 关联性不足。

## 执行步骤
1. 继续审计 `health.py`、`app.py`、`streaming.py`、前端 API 调用路径和当前真实日志文件。
2. 在应用层补齐关键日志：
   - 路由入口/成功摘要
   - SSE start / first event / tool lifecycle / complete / error
   - todos/history 读取摘要
   - health 请求摘要（仅关键字段，避免噪音）
3. 重启后端，执行真实请求，保存日志证据到 `trash/run/`。
4. 输出总结与残留风险。
