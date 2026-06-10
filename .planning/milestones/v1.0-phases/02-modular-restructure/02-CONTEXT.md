# Phase 2+: 模块化重组 - Context

**Gathered:** 2026-03-26
**Status:** Ready for planning

<domain>
## Phase Boundary

将现有单体后端（592 行 app.py + 597 行 tools.py）重组为清晰的模块结构。**不是**构建抽象框架，而是整理代码让可替换部分（tools、prompts、config、middleware）与核心部分（SSE 流式、路由、lifespan）分离。

新用户 fork 项目后只需替换 tools/、prompts/、middleware.py 和 config.py 即可搭建自己的 Agent 服务。

</domain>

<decisions>
## Implementation Decisions

### 整体架构方向（重大调整）
- **不采用** agent_core/ + projects/ 双层架构
- **不定义** Protocol 接口（KnowledgeBaseProtocol、PromptProvider 等）
- **不使用** pydantic-settings 配置继承
- **采用**模块拆分方案：在现有项目内重组目录结构，把可替换部分拆成独立模块

### 目标目录结构
```
fault-diagnosis/
├── app.py                  # 瘦身: lifespan + 路由 + SSE (~250行)
├── config.py               # 🔄 配置集中管理
├── utils.py                # 通用工具 (sanitize_json, todo解析)
├── knowledge_base.py       # KB逻辑 (从 config 读取参数)
├── tools/                  # 🔄 所有工具
│   ├── __init__.py         #   导出 tools 列表
│   ├── data_tools.py       #   extract_data + fig_inter + python_inter
│   ├── sql_tools.py        #   sql_inter + SQL toolkit
│   ├── kb_tools.py         #   query_knowledge_base
│   ├── report_tools.py     #   save_report + save_html_report
│   ├── utility_tools.py    #   get_time + search_tool
│   └── subagent/           #   子agent作为工具
│       ├── __init__.py
│       ├── agent.py
│       ├── system_prompt.py
│       └── api_tool.py
├── prompts/                # 🔄 提示词
│   ├── system_prompt.py    #   systemprompt
│   └── dynamic_prompt.py   #   Context + @dynamic_prompt
├── middleware.py           # 🔄 中间件组装
└── tests/
```

### globals() 共享命名空间
- extract_data、fig_inter、python_inter 三个工具通过 `globals()` 共享 Python 运行时状态
- `extract_data` 把 DataFrame 写入模块 globals，`fig_inter` 和 `python_inter` 从 globals 读取
- **决策：三个工具保持在同一文件** `tools/data_tools.py`
- `data_tools.py` 顶部 import matplotlib/plt/pd/sns，globals() 自动指向该模块命名空间
- Agent 调用工具不关心定义位置，只要在 tools 列表里即可

### 模块级 DB 连接
- **现有问题：** `tools.py:32-48` 在 import 时创建 MySQL 连接和 SQLDatabaseToolkit
- **决策：重构时改为延迟初始化** — 在 lifespan 或首次调用时建连接，不在 import 时
- 这也解决了 Phase 1 测试需要重度 mock 的问题

### 子 Agent
- 现有 `subagent/` 目录移到 `tools/subagent/` 下
- 保持以 Tool 形式封装（fault_explanation_tool）
- 子 agent 内部的工具（call_api_tool、fig_inter）和 prompt 一起迁移

### 生命周期与模块导入
- 工具函数 import 只是定义（不执行），lifespan 时组装成 agent，请求时才调用
- 顺序：import 定义 → lifespan 组装 → 请求时执行，无冲突
- 延迟初始化后，import 阶段不会触发任何外部连接

### Claude's Discretion
- config.py 的具体字段和组织方式
- utils.py 中包含哪些通用函数
- middleware.py 的具体实现方式
- app.py 瘦身后的内部组织（SSE 函数是否拆文件等）
- knowledge_base.py 如何从 config.py 读取参数

</decisions>

<code_context>
## Existing Code Insights

### app.py 构成分析（592行）
| 区块 | 行数 | 内容 | 属性 |
|------|------|------|------|
| 1-36 | 36 | import + 环境变量 | 基础 |
| 40-57 | 18 | Context + @dynamic_prompt | 项目特定 → prompts/ |
| 59-202 | 144 | python_inter / extract_data / fig_inter | 项目特定 → tools/data_tools.py |
| 204-219 | 16 | model 创建 | 配置 → config.py |
| 221-280 | 60 | lifespan（PG连接池+中间件+agent创建） | 核心，留在 app.py |
| 282-430 | 149 | FastAPI app + SSE 流式 | 核心，留在 app.py |
| 432-539 | 108 | API 路由（chat/stream, history, todos） | 核心，留在 app.py |
| 541-578 | 38 | 静态文件挂载 + 入口 | 核心，留在 app.py |

### tools.py 构成分析（597行）
| 区块 | 行数 | 内容 | 去向 |
|------|------|------|------|
| 1-22 | 22 | import + 环境变量 | 各模块自行 import |
| 24-48 | 25 | 模块级 DB 连接 + SQL toolkit | tools/sql_tools.py（改延迟初始化） |
| 50-82 | 33 | query_knowledge_base | tools/kb_tools.py |
| 84-142 | 59 | sql_inter | tools/sql_tools.py |
| 144-189 | 46 | fault_explanation_tool + get_time | tools/subagent/ + tools/utility_tools.py |
| 191-387 | 197 | save_report + save_html_report | tools/report_tools.py |
| 389-454 | 66 | sanitize_for_json + safe_json_dumps | utils.py |
| 456-583 | 128 | todo 解析函数族 | utils.py |
| 585-597 | 13 | tools 列表组装 | tools/__init__.py |

### 需要外化的硬编码值
| 值 | 当前位置 | 迁移到 config.py |
|---|----------|-----------------|
| Ollama URL `http://10.108.13.254:11434` | knowledge_base.py:26 | ollama_base_url |
| Embedding model `qwen3-embedding:8b` | knowledge_base.py:25 | embedding_model |
| FAISS path `faiss_db` | knowledge_base.py:15 | faiss_path |
| max_tokens_before_summary `64000` | app.py:245 | max_tokens_before_summary |
| messages_to_keep `20` | app.py:246 | messages_to_keep |
| recursion_limit `50` | app.py:321 | recursion_limit |
| ML API URL `http://10.108.13.250:8001/predict_reason` | subagent/call_api_tool.py:91 | fault_api_url |
| DCMA db name `dcma` | tools.py:35 | dcma_db_name |

</code_context>

<specifics>
## Specific Ideas

- 新用户使用流程：fork → 改 tools/ 里的工具 → 改 prompts/ 里的提示词 → 改 config.py 里的配置 → 运行
- 子 agent 也是一种工具，统一放在 tools/ 下管理
- 延迟初始化不仅是代码整洁问题，也直接改善了测试体验（不需要在 import 前 mock 数据库）

</specifics>

<deferred>
## Deferred Ideas

- pydantic-settings 配置验证（当前用简单的 dataclass 或 dict 即可，后续可升级）
- Tool 自动发现机制（扫描 tools/ 目录自动注册）
- 项目脚手架模板（cookiecutter 一键创建）

</deferred>

---

*Phase: 02-modular-restructure*
*Context gathered: 2026-03-26*
