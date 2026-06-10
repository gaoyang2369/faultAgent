# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Behavioral Guidelines

### 1. Think Before Coding

**不要假设，不要隐藏困惑，主动暴露权衡。**

实现之前：
- 明确陈述你的假设。如果不确定，先问。
- 如果存在多种解读，列出来 — 不要默默选一个。
- 如果有更简单的方案，说出来。必要时提出反对意见。
- 如果有不清楚的地方，停下来，指出困惑所在，然后问。

### 2. Simplicity First

**用最少的代码解决问题，不做推测性设计。**

- 不做超出需求的功能。
- 不为只用一次的代码建抽象。
- 不加未被要求的"灵活性"或"可配置性"。
- 不为不可能发生的场景做错误处理。
- 如果写了 200 行但 50 行就能搞定，重写。

自问："一个高级工程师会觉得这过于复杂吗？"如果是，简化。

### 3. Surgical Changes

**只改必须改的。只清理自己造成的混乱。**

编辑已有代码时：
- 不要"顺手改进"相邻的代码、注释或格式。
- 不要重构没坏的东西。
- 匹配现有风格，即使你会用不同的方式。
- 如果发现无关的死代码，提一下 — 不要删。

当你的修改产生了孤立代码时：
- 移除你的修改导致不再使用的 import/变量/函数。
- 不要移除已有的死代码，除非被要求。

检验标准：每一行改动都应该能直接追溯到用户的需求。

### 4. Goal-Driven Execution

**定义成功标准，循环验证直到确认。**

将任务转化为可验证的目标：
- "添加验证" → "为无效输入写测试，然后让测试通过"
- "修复 bug" → "写一个复现测试，然后让它通过"
- "重构 X" → "确保重构前后测试都通过"

对于多步骤任务，列出简要计划：
```
1. [步骤] → 验证: [检查点]
2. [步骤] → 验证: [检查点]
3. [步骤] → 验证: [检查点]
```

强成功标准让你可以独立循环。弱标准（"让它能跑"）需要不断澄清。

## Language & Style

- **所有注释、文档字符串、用户可见字符串和系统提示词使用简体中文**
- 状态前缀: `✅` 成功, `❌` 错误, `⚠️` 警告, `🚀` 启动
- Python: `snake_case` 文件/函数, 4 空格缩进, 双引号优先
- Vue: `PascalCase.vue` 组件, `camelCase.ts` composables 以 `use` 开头

## Constraints

- **Tech Stack**: LangChain 1.0.3 + LangGraph 1.0.5 + FastAPI 0.121.0 — 不要升级
- **API 契约**: 不要修改已有 HTTP 端点 — 前端依赖其保持不变
- **不引入重量级新依赖** — 保持项目轻量
- **exec 安全**: 任何涉及 `exec()` 的修改必须保留 `_audit_code()` AST 审计和空 `__builtins__` 沙箱，同步更新 `tests/test_data_tools.py`

## Build & Run

```bash
conda activate faultagent                    # Python 3.12
python -m fault_diagnosis.app                # Dev server :8000
LOCAL_DEV_MODE=true python -m fault_diagnosis.app  # 跳过外部依赖
cd agent_fronted && npm run dev              # Frontend :9005
pytest                                       # 全部测试
pytest tests/test_data_tools.py -v           # 安全沙箱测试
python rebuild_kb.py                         # 重建 FAISS 索引
```

Production: `gunicorn -w 4 -k uvicorn.workers.UvicornWorker fault_diagnosis.app:app --bind 0.0.0.0:8000`

## Architecture

工业设备故障诊断 AI Agent 系统。LangGraph ReAct Agent 通过中间件管线处理消息，调用工具，SSE 流式返回。

```
Frontend (Vue 3) → SSE/REST → FastAPI (app.py) → LangGraph Agent → Tools → MySQL/FAISS/External APIs
```

### 模块

| 模块 | 职责 |
|------|------|
| `app.py` | FastAPI 入口、lifespan、路由、会话管理 |
| `streaming.py` | SSE token 级流式事件生成器 |
| `middleware.py` | 组装 TodoList + DynamicPrompt + Summarization |
| `config.py` | 集中配置，支持环境变量覆盖 |
| `session_scope.py` | Cookie 签名、thread 归属校验、旧 ID 兼容映射 |
| `session_store.py` | `contextvars` 请求级命名空间隔离 |
| `db_pool.py` | async MySQL 连接池生命周期 |
| `knowledge_base.py` | FAISS 向量库（Ollama 嵌入） |
| `logger.py` | 结构化 JSON 日志 + request_id |
| `dev_mode.py` | 本地开发模式 |
| `tools/` | 搜索、知识库、报告、SQL、时间查询 |
| `robot_arm/` | 机械臂模块（`ENABLE_ROBOT_ARM=true` 启用） |
| `prompts/` | 系统提示词 + 动态角色注入 |

所有模块路径前缀为 `fault_diagnosis/`，例如 `fault_diagnosis/app.py`。

### 端点

| 路由 | 说明 |
|------|------|
| `GET /chat/stream` | SSE 流式聊天（`message`, `thread_id`, `user_identity`） |
| `GET /ai/history/{type}` | 会话列表 |
| `GET /ai/history/{type}/{chat_id}` | 指定会话消息 |
| `GET /api/todos/{thread_id}` | 任务列表及统计 |

SSE 事件: `start` → `token` → `tool_start`/`tool_end` → `complete`（异常时 `server_error`）

### 数据库

- **MySQL** — 业务数据，通过 `db_pool.py` 异步连接池查询
- **PostgreSQL** — LangGraph 状态持久化（`AsyncPostgresSaver`，context manager 管理生命周期，不要手动 `pool.close()`）

### 工具注册

默认: `get_search_tool()`, `query_knowledge_base`, `save_report`, `save_html_report`, `get_time` + `get_sqltools()`

机械臂（需 `ENABLE_ROBOT_ARM=true`）: `sql_inter`, `fault_explanation_tool`, `extract_data`, `fig_inter`

`get_runtime_tools()` 统一返回当前部署的完整工具列表。

## Key Patterns

- **Tool 定义**: `BaseModel` schema + `@tool(args_schema=...)` 装饰器，docstring 用中文作为 LLM 工具描述
- **命名空间隔离**: `extract_data` / `fig_inter` 通过 `session_store.get_namespace()` (`contextvars`) 共享 DataFrame
- **exec 沙箱**: `_audit_code()` AST 审计 + `{"__builtins__": {}}` 空沙箱，双层防护
- **Sub-agent**: `fault_explanation_tool` 是 `async def`，用 `await sub_agent.ainvoke()`
- **惰性加载**: `get_search_tool()` 工厂延迟实例化 TavilySearch；`_get_db()` 单例延迟初始化 SQL
- **前端别名**: `@` → `agent_fronted/src/`

## Configuration

敏感配置在 `.env`（不要读取 — 含密钥）。非敏感配置在 `config.py`，支持环境变量覆盖。

- **`.env`**: MySQL (`HOST`, `USER`, `MYSQL_PW`, `DB_NAME`, `PORT`)、PostgreSQL (`POSTGRES_*`)、LLM (`MODEL_NAME`, `OPENAI_BASE_URL`, `OPENAI_API_KEY`)、`SESSION_SECRET`、`TAVILY_API_KEY`
- **功能开关**: `ENABLE_ROBOT_ARM`（默认 false）、`LOCAL_DEV_MODE`（默认 false）

## Active Refactoring

项目正从单体重构为模块化架构，详见 `.planning/ROADMAP.md`。阶段状态在 `.planning/STATE.md`。
