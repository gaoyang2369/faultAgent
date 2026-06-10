# Phase 3: Tools Modularization - Context

**Gathered:** 2026-03-26
**Status:** Ready for planning

<domain>
## Phase Boundary

将单体 tools.py + app.py 中的工具定义拆分到 tools/ 目录，模块级 DB 连接改为延迟初始化，subagent/ 迁移到 tools/subagent/。旧的 tools.py 和 subagent/ 目录删除。

不新增功能，不改变 API 行为，前端无需修改。

</domain>

<decisions>
## Implementation Decisions

### tools/ 文件分组

按关注点拆为 5 个文件 + subagent 子目录：

```
tools/
├── __init__.py          # 显式导入各模块工具，拼接 tools 列表导出
├── data_tools.py        # extract_data, fig_inter (globals() 共享)
├── sql_tools.py         # sql_inter, sqltools (SQLDatabaseToolkit 生成)
├── kb_tools.py          # query_knowledge_base
├── report_tools.py      # save_report, save_html_report
├── utility_tools.py     # get_time, search_tool (TavilySearch)
└── subagent/
    ├── __init__.py      # fault_explanation_tool 定义 + 导出
    ├── agent.py         # create_fault_explanation_agent()
    ├── system_prompt.py # FAULT_EXPLANATION_SYSTEM_PROMPT
    └── api_tool.py      # query_fault_data_and_call_api, fig_inter(subagent版), tools列表
```

- **python_inter 删除**：当前未注册到 tools 列表（标记为"暂时禁用"），迁移时删除死代码
- **fault_explanation_tool 放 tools/subagent/__init__.py**：它本质是子 Agent 入口，和子 Agent 实现放一起更内聚
- **search_tool (TavilySearch) 放 utility_tools.py**：作为通用第三方搜索工具
- **sqltools (SQLDatabaseToolkit) 放 sql_tools.py**：和 sql_inter 同属 SQL 查询职责

### 延迟初始化策略

- **方式**：懒加载单例模式（模块级 `_db = None` + `_get_db()` / `get_sqltools()` 函数）
- **scope**：
  - tools/sql_tools.py：`SQLDatabase.from_uri()` + `ChatOpenAI()` + `SQLDatabaseToolkit()` 全部延迟
  - tools/subagent/api_tool.py：同样独立懒加载，不复用 sql_tools 的连接
- **sql_inter 内部 pymysql 连接保持现状**：每次调用新建短命连接并在 finally 中关闭，这是故意设计

### Subagent 重组

- **两个 fig_inter 保持独立**：主 Agent 版（data_tools.py）用 sns/pd，子 Agent 版（api_tool.py）用 np，依赖不同、路径不同、返回值不同，各自保留
- **文件重命名**：fault_explanation_agent.py → agent.py，fault_explanation_system_prompt.py → system_prompt.py，call_api_tool.py → api_tool.py
- **测试 CLI 删除**：agent.py 中的 invoke_fault_explanation_agent() 和 `if __name__ == '__main__'` 块删除
- **api_style.md**：跟随迁移到 tools/subagent/
- **__file__ 路径调整**：直接修改 dirname 层级数
  - tools/ 下文件：`dirname(dirname(__file__))` → 项目根
  - tools/subagent/ 下文件：`dirname(dirname(dirname(__file__)))` → 项目根

### tools 列表组装

- **__init__.py 显式导入**：从每个子模块显式 import 工具函数，拼成 tools 列表
- **sqltools 延迟加入**：__init__.py 的 tools 列表不含 sqltools，导出 `get_sqltools()` 函数
- **app.py lifespan 中 extend**：`tools.extend(get_sqltools())` 在 DB 就绪后调用
- **app.py 导入方式不变**：`from tools import tools` + `from tools.sql_tools import get_sqltools`
- **app.py 不再定义任何工具**：extract_data、fig_inter 移走后 app.py 中无工具代码，不再有 `tools.extend([...])`

### Claude's Discretion

- html_template.html 的引用路径调整（report_tools.py 中 __file__ 层级变化）
- conftest.py 中 mock patch 路径适配新 tools/ 结构
- 各工具函数的 import 语句具体写法

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- **config.py**（Phase 2 创建）：DCMA_DB_NAME、FAULT_API_URL 等常量已集中，新模块直接导入
- **utils.py**（Phase 2 创建）：sanitize_for_json、safe_json_dumps 等已独立，无需重复提取
- **conftest.py**：已有完整的 mock 基础设施（SQLDatabase.from_uri、SQLDatabaseToolkit、TavilySearch、knowledge_base），迁移后需更新 patch 路径

### Established Patterns
- **Tool 定义模式**：Pydantic BaseModel schema + @tool(args_schema=...) 装饰器，中文 docstring
- **懒加载先例**：knowledge_base.py 的 db_retriever 就是模块级变量 + init 函数模式
- **globals() 共享**：extract_data 存 DataFrame，fig_inter 通过 globals() 读取，必须同文件

### Integration Points
- **app.py:33** — `from tools import tools`，迁移后路径不变（tools 变成包）
- **app.py:204** — `tools.extend([extract_data, fig_inter])`，迁移后删除此行
- **app.py lifespan** — 需要新增 `tools.extend(get_sqltools())` 调用
- **conftest.py:99-106** — mock patch 路径需要从 `langchain_community.utilities.SQLDatabase.from_uri` 等适配

</code_context>

<specifics>
## Specific Ideas

- 旧 tools.py 删除后，其中 `from config import DCMA_DB_NAME` 移到 sql_tools.py
- 旧 subagent/ 删除时确认没有其他文件引用它（只有 tools.py 的 `from subagent.fault_explanation_agent import`）
- Phase 1 的 22 个测试 + Phase 2 的 52 个测试都必须继续通过

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 03-tools-modularization*
*Context gathered: 2026-03-26*
