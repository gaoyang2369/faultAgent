# Phase 1: Safety Net - Context

**Gathered:** 2026-03-26
**Status:** Ready for planning

<domain>
## Phase Boundary

在任何代码移动或重构之前，建立特征测试套件和安全前置条件。包括：SSE 流式事件序列测试、工具调用测试、历史 API 测试、冒烟测试，以及密钥泄露清理。

</domain>

<decisions>
## Implementation Decisions

### 测试隔离策略
- **全 Mock**：Mock 掉 LLM、MySQL、PostgreSQL、Ollama 所有外部服务
- `tools.py` 模块级 DB 连接用 pytest monkeypatch 在 import 前 mock 掉 `pymysql.connect` 和 `SQLDatabase`
- 不修改生产代码来适配测试（延迟初始化属于 Phase 4 TOOL-02 的工作）
- 测试框架：pytest + pytest-asyncio，测试文件放在项目根目录 `tests/` 下，配 `conftest.py` 和 `pytest.ini`
- httpx 已在 requirements.txt 中，用 FastAPI TestClient 进行端点测试

### SSE 特征测试
- **Mock LLM 响应**：用 fake model 返回固定的 tool_call 消息，让 agent 确定性地调用工具
- **断言粒度：事件类型 + 结构**：断言事件类型序列（start → token → tool_start → tool_end → complete）和每种事件的 JSON 结构（必有字段），不断言具体文本内容
- 至少测试 get_time 工具调用产生的完整 SSE 事件序列

### 密钥清理
- 删除 `app_copy.py`
- 清理 `subagent/fault_explanation_agent.py` 中注释掉的 API key 和旧配置块
- 顺便修复 `subagent/fault_explanation_agent.py` 缺失的 `import os`
- 不重写 git 历史（不用 BFG/filter-branch），而是轮换已泄露的密钥
- 验证：`git log --all -S "sk-"` 在当前文件中不返回结果（历史中的记录通过密钥轮换来消除风险）

### 冒烟测试
- **pytest 测试形式**，和其他特征测试统一运行
- 验证标准：`from app import app` 不报错 + GET `/chat/stream` 返回 200 并产生 SSE 事件
- 用 Mock LLM 保证确定性

### Claude's Discretion
- pytest fixture 的具体实现方式（如何 mock LangChain model）
- conftest.py 中 monkeypatch 的作用域和顺序
- 测试文件的命名和组织方式

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `httpx` 已在 requirements.txt 中，可直接用于 FastAPI TestClient
- `app.py:447` 的 `stream_chat_log_get()` 是 SSE 入口，返回 `StreamingResponse`
- `app.py:311-444` 的 `token_stream_events()` 是 SSE 事件生成器，产出 6 种事件类型

### Established Patterns
- SSE 事件格式：`data: {"type": "start|token|tool_start|tool_end|complete|server_error", ...}\n\n`
- Agent 创建：`create_agent(model, tools, checkpointer, middleware, context_schema)` in `app.py:264`
- 工具定义：Pydantic BaseModel schema + `@tool(args_schema=...)` 装饰器

### Integration Points
- `tools.py:33-37`：模块级 `SQLDatabase` 和 `SQLDatabaseToolkit` 创建，需要在 import 前 mock
- `knowledge_base.py:31`：全局 `db_retriever` 变量，import 时连接 Ollama
- `app.py:236-294`：lifespan 中创建 PostgreSQL 连接池和 agent，需要 mock
- `subagent/fault_explanation_agent.py:34-38`：缺失 `import os` 会导致运行时 NameError

</code_context>

<specifics>
## Specific Ideas

- Mock LLM 应该能返回带 tool_call 的消息，触发 agent 的工具调用循环
- 特征测试重点是捕获 SSE 事件的"契约"——类型、顺序、结构字段——而非 LLM 生成的具体文本
- 密钥轮换由用户手动完成，代码侧只负责删除硬编码值

</specifics>

<deferred>
## Deferred Ideas

None — 讨论全程保持在 Phase 1 范围内

</deferred>

---

*Phase: 01-safety-net*
*Context gathered: 2026-03-26*
