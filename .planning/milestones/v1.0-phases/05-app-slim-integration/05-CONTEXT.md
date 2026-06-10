# Phase 5: App Slim & Integration - Context

**Gathered:** 2026-03-26
**Status:** Ready for planning

<domain>
## Phase Boundary

app.py 瘦身到只包含核心逻辑（lifespan + SSE路由 + API路由），行数不超过 300 行。端到端验证所有功能不变，前端无需任何修改。

不新增功能，不改变 API 行为，不引入新的架构模式（如 APIRouter 路由分离）。

</domain>

<decisions>
## Implementation Decisions

### SSE 流式函数提取
- **创建 `streaming.py`**，从 app.py 提取 `token_stream_events` 函数（134行）
- streaming.py 只包含 `token_stream_events` 一个函数，不包含路由处理
- streaming.py 直接 `from prompts.dynamic_prompt import Context`，不通过参数传递
- streaming.py 还需导入 `sanitize_for_json`、`safe_json_dumps`、`parse_todos_from_tool_output` 从 utils
- streaming.py 导入 `RECURSION_LIMIT` 从 config
- app.py 中 `from streaming import token_stream_events`
- **路由处理函数 `stream_chat_log_get` 留在 app.py**，因为需要 `@app.get` 装饰器

### Model 创建位置
- **保持模块级创建**，两个 ChatOpenAI 实例留在 app.py 顶部（第34-49行）
- 不移入 lifespan，不提取到 config.py
- 原因：已经可以正常工作，conftest.py 已 mock 了 ChatOpenAI，移动会破坏现有 mock 路径

### 路由组织方式
- **所有路由留在 app.py**，不使用 APIRouter 拆分
- 提取 SSE 后 app.py 约 267 行，已满足 ≤300 行要求
- 路由是 app.py 的核心职责：/chat/stream、/ai/history/{type}、/ai/history/{type}/{chat_id}、/api/todos/{thread_id}

### 端到端验证策略
- **Phase 1 的 22 个测试全部通过** — 这是 safety net 的设计目的
- **`python -c "from app import app"` 成功** — 基本导入验证
- 不新增测试，不做手动前端验证（现有测试已覆盖 SSE 事件、工具调用、API 端点）

### Claude's Discretion
- streaming.py 内部的 import 组织方式
- conftest.py 中是否需要调整 mock 路径（如果 streaming.py 的导入改变了 patch 目标）
- app.py 提取后的内部注释和分段组织

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- **config.py**：已有 RECURSION_LIMIT 常量，streaming.py 可直接导入
- **utils.py**：已有 sanitize_for_json、safe_json_dumps、parse_todos_from_tool_output
- **prompts/dynamic_prompt.py**：已有 Context dataclass
- **middleware.py**：已有 build_middleware()，app.py lifespan 直接调用

### Established Patterns
- **模块拆分模式**：Phase 2-4 已建立了 "提取到独立文件 → app.py import → 删除旧代码 → 跑测试" 的流程
- **conftest.py mock**：使用 `sys.modules` 注入和 `unittest.mock.patch` 来 mock 外部依赖
- **config.py 风格**：模块级常量 + os.getenv() 带默认值

### Integration Points
- **app.py:121-254** — token_stream_events 完整提取到 streaming.py
- **app.py:27** — 新增 `from streaming import token_stream_events`
- **conftest.py** — 可能需要检查是否有直接 patch `app.token_stream_events` 的地方

### 提取后 app.py 预估结构
```
app.py (~267行)
├── Imports (30行)
├── load_dotenv (2行)
├── Model creation (16行)
├── Lifespan (54行)
├── FastAPI app + CORS (14行)
├── stream_chat_log_get 路由 (28行)
├── History API 路由 (30行)
├── Todos API 路由 (47行)
├── 静态文件挂载 (12行)
├── Root endpoint (12行)
└── __main__ (11行)
```

</code_context>

<specifics>
## Specific Ideas

- 这是整个重构的最后一个 Phase，完成后 app.py 从原始 592 行瘦身到 ~267 行
- 提取后的模块结构完全匹配 Phase 2 CONTEXT.md 中规划的目标目录结构
- streaming.py 是纯异步生成器，无模块级副作用，import 安全

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 05-app-slim-integration*
*Context gathered: 2026-03-26*
