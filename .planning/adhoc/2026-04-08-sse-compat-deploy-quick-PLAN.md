---
mode: quick
date: 2026-04-08
owner: codex
status: in_progress
objective: 在保留 SSE 安全修复的前提下，补齐旧历史最小兼容层、部署配置收口和协议残留清理，降低上线行为变化。
---

# SSE 兼容 / 部署收口计划

## 必须保留的安全边界
- 不恢复前端动态求值
- 不重新放开危险 HTML 事件属性
- 后端错误继续只返回通用消息与 `error_id`
- EventSource 保持单活并禁止危险自动重连
- 服务端继续拒绝直接信任前端 `thread_id` / `chat_id` / `user_identity`

## 本轮实施范围
1. 为旧格式 `thread_id` 增加最小兼容层：
   - 识别 legacy `thread_id`
   - 为当前浏览器会话维护受控 legacy → signed thread 映射
   - 无法安全回收的旧历史改为“本地缓存只读 + 新会话接管”降级
2. 收口部署配置：
   - 区分开发/生产环境
   - 收紧 cookie 参数默认值
   - 对 `SESSION_SECRET` / `FRONTEND_ORIGINS` 缺失给出明确行为
3. 清理旧 `/chat` 非流式残留，统一 SSE 主流程

## 验证清单
- pytest：history/todos/SSE/config 兼容测试
- 前端 `npm run build`
- 代码检索：确认旧 `/chat` 主流程残留已清理
