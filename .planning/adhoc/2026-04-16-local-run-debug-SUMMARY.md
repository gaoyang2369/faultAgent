---
mode: debug
date: 2026-04-16
owner: codex
status: completed
objective: 本地跑通项目、修复启动与测试阻塞，并验证前后端最小闭环。
---

# Local Run Debug 总结

## 完成内容
- 完成项目结构、依赖、脚本、配置模板和测试配置审计。
- 安装前端依赖并确认后端 Python 依赖一致。
- 修复本地开发模式环境变量覆盖、SSE 清理异常、懒加载测试隔离和 Vite todos 代理问题。
- 后端测试通过：`116 passed`。
- 前端构建通过：`vue-tsc -b && vite build`。
- 已启动本地开发后端与前端，验证后端根路由、前端首页、SSE、历史、消息详情、todos、Vite 代理和 CORS。

## 关键结论
- 当前项目可以在 `LOCAL_DEV_MODE=true` 下完成无外部依赖的本地联调闭环。
- 真实模式仍依赖 MySQL、PostgreSQL、LLM、Tavily、Ollama 和外部故障诊断 API，缺任一服务都可能阻塞完整诊断链路。
- 前端本地浏览器会直连 `http://127.0.0.1:8000`，Vite `/api` 代理不是主路径。
