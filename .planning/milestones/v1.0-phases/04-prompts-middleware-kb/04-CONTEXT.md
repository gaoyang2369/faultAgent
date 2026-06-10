# Phase 4: Prompts, Middleware & KB - Context

**Gathered:** 2026-03-26
**Status:** Ready for planning

<domain>
## Phase Boundary

将提示词、动态 Prompt、中间件组装逻辑移入独立模块，知识库完全配置化。旧的 prompt_template.py 删除。

不新增功能，不改变 API 行为，前端无需修改。

</domain>

<decisions>
## Implementation Decisions

### prompts/ 目录结构

```
prompts/
├── __init__.py
├── system_prompt.py     # systemprompt 字符串 + get_identity_system_prompt()
└── dynamic_prompt.py    # Context dataclass + identity_aware_prompt (@dynamic_prompt)
```

- **system_prompt.py**：从 prompt_template.py 迁移 `systemprompt` 和 `get_identity_system_prompt()`
- **dynamic_prompt.py**：从 app.py:35-53 迁移 `Context` dataclass 和 `identity_aware_prompt` 函数
- **注释掉的死代码（~70行旧版身份提示词）直接删除**，只保留活跃代码
- app.py 改为 `from prompts.dynamic_prompt import Context, identity_aware_prompt`
- middleware.py 从 `prompts.dynamic_prompt` 导入 `identity_aware_prompt`

### middleware.py 边界

- **纯组装函数**：导出 `build_middleware(summary_model)` → 返回中间件列表
- **不包含 model 创建**：model 和 summary_model 的 ChatOpenAI 创建留在 app.py
- **不包含 agent 创建**：create_agent() 留在 app.py lifespan
- app.py lifespan 调用：`middleware_list = build_middleware(summary_model)`，然后传入 create_agent()

```python
# middleware.py
from langchain.agents.middleware import (
    TodoListMiddleware, SummarizationMiddleware
)
from prompts.dynamic_prompt import identity_aware_prompt
from config import MAX_TOKENS_BEFORE_SUMMARY, MESSAGES_TO_KEEP

def build_middleware(summary_model):
    return [
        TodoListMiddleware(),
        identity_aware_prompt,
        SummarizationMiddleware(
            model=summary_model,
            max_tokens_before_summary=MAX_TOKENS_BEFORE_SUMMARY,
            messages_to_keep=MESSAGES_TO_KEEP,
        ),
    ]
```

### 知识库配置化

- **保留模块级 init_knowledge_base() 调用**：import 时自动初始化，不做延迟初始化改造
- **新增 config.py 参数**：
  - `KB_CHUNK_SIZE = int(os.getenv("KB_CHUNK_SIZE", "3000"))`
  - `KB_CHUNK_OVERLAP = int(os.getenv("KB_CHUNK_OVERLAP", "1000"))`
  - `KB_BATCH_SIZE = int(os.getenv("KB_BATCH_SIZE", "50"))`
- **knowledge_base.py 中的硬编码值改为从 config.py 导入**
- **rebuild_knowledge_base() 的 db_save_path 默认值改用 FAISS_PATH**（不再硬编码 "faiss_db"）
- **KBAS-02（8秒超时保护）**：研究阶段检查 OllamaEmbeddings 的 timeout 配置，确认现有行为是否满足

### 旧文件清理

- **prompt_template.py 删除**：内容已迁移到 prompts/system_prompt.py + prompts/dynamic_prompt.py
- **rebuild_kb.py 适配**：import 路径不变（仍然 `from knowledge_base import rebuild_knowledge_base`）

### Claude's Discretion

- prompts/__init__.py 的导出内容
- conftest.py 中 mock patch 路径适配（如果 prompt 导入路径变化需要更新 mock）
- knowledge_base.py 内部函数参数的具体传递方式

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- **config.py**（Phase 2 创建）：已有 OLLAMA_BASE_URL、EMBEDDING_MODEL、FAISS_PATH、MAX_TOKENS_BEFORE_SUMMARY、MESSAGES_TO_KEEP、RECURSION_LIMIT
- **conftest.py:104-105**：已 mock `knowledge_base.init_knowledge_base` 和 `knowledge_base.create_knowledge_base`
- **conftest.py:53-63**：已 mock `langchain.agents.middleware` 模块（TodoListMiddleware、dynamic_prompt、SummarizationMiddleware）

### Established Patterns
- **config.py 风格**：模块级常量，`os.getenv()` 带默认值，按功能分组注释
- **工具函数定义模式**：中文 docstring，Pydantic schema
- **@dynamic_prompt 装饰器**：接收 ModelRequest，从 request.runtime.context 读取 Context 字段

### Integration Points
- **app.py:29** — `from prompt_template import systemprompt, get_identity_system_prompt`，需改为 `from prompts.system_prompt import ...`
- **app.py:37-52** — Context + identity_aware_prompt 定义，需移到 prompts/dynamic_prompt.py
- **app.py:93-112** — middleware 组装 + agent 创建，middleware 部分提取到 middleware.py
- **tools/kb_tools.py** — `from knowledge_base import db_retriever`，路径不变
- **rebuild_kb.py:2** — `from knowledge_base import rebuild_knowledge_base`，路径不变

</code_context>

<specifics>
## Specific Ideas

- middleware.py 是纯函数模块，无模块级副作用，import 安全
- prompts/ 中 dynamic_prompt.py 需要导入 system_prompt.py 的内容（identity_aware_prompt 内部调用 get_identity_system_prompt + systemprompt）
- 测试中 conftest.py 对 middleware 的 mock 是在 langchain.agents.middleware 层面做的，不受本次重构影响
- 测试中对 knowledge_base 的 mock 路径不变

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 04-prompts-middleware-kb*
*Context gathered: 2026-03-26*
