---
mode: debug
date: 2026-04-16
owner: codex
status: completed
objective: 审计新拉取项目，补齐本地依赖，构建、测试、启动前后端，并验证最小联调闭环。
---

# Local Run Debug 计划

## 当前前提
- 用户要求以真实执行为准，完成依赖安装、构建、测试、启动和核心流程验证。
- 仓库已有 `.env`，其中可能包含密钥，本轮不读取真实 `.env` 内容。
- 真实模式依赖 PostgreSQL、MySQL、LLM、Tavily 和外部故障 API；优先使用 `LOCAL_DEV_MODE=true` 打通无外部依赖闭环，再记录真实模式缺口。

## 本轮目标
- 识别后端、前端、测试和启动脚本的实际运行路径。
- 安装或确认后端 Python 依赖与前端 npm 依赖。
- 执行后端测试、前端构建、可用静态检查。
- 启动后端本地开发模式和前端 dev server，验证首页、SSE 聊天、历史与 todos API。
- 如遇明显配置、路径、依赖或测试夹具问题，做最小侵入修复并回归验证。

## 执行顺序
1. 只读审计 README、依赖文件、脚本、配置模板和测试配置。
2. 确认本机 Python/Node 版本与已有依赖状态。
3. 安装缺失前端依赖，必要时补齐后端依赖。
4. 运行后端测试与前端构建。
5. 启动本地开发后端和前端，执行 API 与页面探针。
6. 汇总修改、验证结果、阻塞项和下一步建议。

## 风险控制
- 不读取真实 `.env` 内容，不输出密钥。
- 不升级约束版本，不引入重量级新依赖。
- 不改变既有 HTTP API 契约。
- 所有 mock、降级和跳过项必须显式记录。

## 实际执行结果
1. 已确认技术栈为 FastAPI/LangGraph 后端 + Vue 3/Vite 前端，真实模式依赖 MySQL、PostgreSQL、LLM、Tavily、Ollama 和外部故障 API。
2. 已安装前端依赖，后端 Python 依赖通过 `pip check`。
3. 已修复 `LOCAL_DEV_MODE=true` 被 `.env` 覆盖的问题，本地开发模式现在会跳过真实 PostgreSQL / MySQL / 外部服务初始化。
4. 已修复 SSE 开发模式结束时清理未初始化 `agent_stream` 的异常。
5. 已修复懒加载测试的模块缓存隔离问题，避免前序测试导入机械臂模块后误判。
6. 已修复 Vite `/api/todos` 代理被通用 `/api` rewrite 错误改写的问题。
7. 已完成验证：
   - 后端测试：`116 passed`
   - 前端构建：`npm run build` 通过
   - 后端服务：`http://127.0.0.1:8000` 监听成功
   - 前端服务：`http://127.0.0.1:9005` 监听成功
   - 联调闭环：SSE、历史列表、消息详情、todos、Vite 代理、CORS 预检均返回 200

## 剩余问题
- 真实模式仍需要可用 MySQL、PostgreSQL、LLM、Tavily、Ollama 和外部故障诊断 API 配置。
- `npm install` 报告 8 个依赖审计漏洞，本轮未自动升级依赖以避免破坏锁定版本兼容性。
- 前端没有独立 lint、unit test 或 e2e 脚本；当前只能通过 `vue-tsc -b && vite build` 做静态和构建验证。
