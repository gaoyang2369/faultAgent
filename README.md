# 工业设备故障诊断专家系统

基于 LangGraph ReAct Agent 的工业设备智能诊断平台。当前主系统以 **DCMA** 为默认核心能力，围绕运行状态、异常排查、故障码检索和报告生成提供稳定的 SSE 流式分析链路；机械臂能力已隔离到独立模块，默认不再接入主流程。

**核心能力：**

- **DCMA 运行诊断**：围绕 DCMA 数据库、知识库和报告模板生成运行概览、异常分析和处置建议
- **双格式报告**：Markdown 技术报告 + HTML 交互式报告（ECharts 图表 + KPI 卡片）
- **知识库增强**：FAISS 向量检索设备手册和故障码，配合网络搜索补充最新标准
- **对话持久化**：PostgreSQL 存储完整对话状态，支持多轮上下文和历史回溯
- **最小会话隔离**：服务端签发 session cookie 和受控 `thread_id`，避免前端直接伪造会话归属
- **身份适配**：根据用户身份（游客/管理员）动态调整响应的专业程度
- **机械臂隔离模块**：机械臂数据预览、图表和 SHAP 子 Agent 已迁入 `fault_diagnosis/robot_arm/`，仅在 `ENABLE_ROBOT_ARM=true` 时启用

## 当前状态

- **源码结构已完成收敛**：后端真实实现统一位于 `fault_diagnosis/`，根目录兼容层和兼容包已经退休。
- **官方后端入口已切换**：开发入口为 `python -m fault_diagnosis.app`，生产入口为 `gunicorn ... fault_diagnosis.app:app`。
- **仓库当前验证基线**：后端测试 `105 passed`，前端 `npm run build` 通过，前后端探针均返回 `200`。
- **聊天主链路已补强**：SSE 工具事件不再把 SQL 草稿或对象垃圾文本直接渲染到用户正文。
- **刷新历史恢复已热修**：历史消息角色已统一为 `user/assistant/tool/system`，同浏览器刷新时会优先使用本地缓存修补服务端已退化的旧 user 文本。
- **当前已知边界**：默认仓库自带的 `faiss_db/` 只是一份 2 chunk smoke index，用于检索链路自测，不代表正式全量知识库；如需正式 RAG 效果，请按下文执行 full rebuild。

## 系统架构

```
┌─────────────────┐     SSE/REST      ┌──────────────────────────────────────┐
│  Vue 3 Frontend │ ◄───────────────► │  FastAPI (fault_diagnosis.app)       │
│  :9005          │                    │  :8000                               │
└─────────────────┘                    │                                      │
                                       │  ┌─────────────────────────────────┐ │
                                       │  │ LangGraph ReAct Agent           │ │
                                       │  │                                 │ │
                                       │  │ Middleware Pipeline:            │ │
                                       │  │ TodoList → DynamicPrompt →     │ │
                                       │  │ Summarization                   │ │
                                       │  │                                 │ │
                                       │  │ Default Tools (5个):            │ │
                                       │  │ ┌─────────┐ ┌──────────┐       │ │
                                       │  │ │ SQL查询  │ │ 知识库    │       │ │
                                       │  │ │ 报告生成 │ │ 网络搜索  │       │ │
                                       │  │ │ 时间工具 │ │          │       │ │
                                       │  │ └─────────┘ └──────────┘       │ │
                                       │  └─────────────────────────────────┘ │
                                       │  ┌─────────────────────────────────┐ │
                                       │  │ Optional Module:                │ │
                                       │  │ fault_diagnosis/robot_arm/      │ │
                                       │  │ sql_inter / extract_data /      │ │
                                       │  │ fig_inter / fault_explanation   │ │
                                       │  └─────────────────────────────────┘ │
                                       │                                      │
                                       │  ┌──────┐ ┌──────┐ ┌──────┐        │
                                       │  │MySQL │ │PgSQL │ │FAISS │        │
                                       │  │业务数据│ │会话状态│ │知识库 │        │
                                       │  └──────┘ └──────┘ └──────┘        │
                                       └──────────────────────────────────────┘
```

**SSE 事件流**：`start` → `token`(逐字输出) → `tool_start`/`tool_end`(工具调用) → `complete`

## 项目结构

```
.
├── rebuild_kb.py           # 知识库重建脚本
├── fault_diagnosis/        # 后端主源码根（本次结构收敛后的真实实现）
│   ├── app.py              # FastAPI 入口：lifespan、路由、CORS、静态文件
│   ├── streaming.py        # SSE 流式事件生成器（含心跳保活）
│   ├── config.py           # 集中配置（环境变量 + 默认值）
│   ├── utils.py            # 通用工具函数（JSON 序列化、todo 解析）
│   ├── middleware.py       # 中间件组装（TodoList + DynamicPrompt + Summarization）
│   ├── knowledge_base.py   # FAISS 知识库（创建/加载/检索）
│   ├── logger.py           # 结构化日志（JSON + request_id）
│   ├── session_store.py    # 会话级命名空间（contextvars）
│   ├── db_pool.py          # 全局异步 MySQL 连接池（aiomysql.Pool）
│   ├── dev_mode.py         # 本地开发模式（跳过外部依赖）
│   ├── tools/              # 主系统工具模块（真实实现）
│   ├── prompts/            # 提示词模块（真实实现）
│   └── robot_arm/          # 机械臂隔离模块（真实实现）
├── .env.example            # 环境变量模板
├── requirements.txt        # Python 依赖清单
├── pytest.ini              # pytest 配置
├── .gitignore              # Git 忽略规则（缓存 / 构建产物 / 日志）
├── AGENTS.md               # Codex 协作与仓库约束说明
├── CLAUDE.md               # Claude 协作说明
├── scripts/                # 启动 / 测试辅助脚本
│   ├── clean_garbage.ps1
│   ├── run_backend.ps1
│   ├── run_backend.py
│   ├── run_frontend_dev.ps1
│   ├── run_local_dev.ps1
│   ├── run_local_dev.py
│   └── run_tests.ps1
├── templates/              # 报告模板资源
│   ├── html_template.html
│   └── md_template.md
├── .planning/              # GSD 规划/执行记录、阶段状态与重构路线
│   ├── ROADMAP.md
│   ├── STATE.md
│   ├── REQUIREMENTS.md
│   └── adhoc/              # 每轮 quick/debug/phase 执行记录
├── docs/                   # 辅助文档
│   └── codebase_analysis_report.md
│
├── tests/                  # 测试套件（105 个测试用例，全绿 ✅）
│   ├── conftest.py         #   fixtures: mock Agent / checkpointer / dev_mode
│   ├── fake_model.py       #   测试用 FakeLLM 模拟器
│   ├── helpers.py          #   SSE 解析辅助函数
│   ├── test_smoke.py       #   冒烟测试（import / 路由 / SSE）
│   ├── test_config.py      #   配置模块测试
│   ├── test_utils.py       #   utils.py 全覆盖
│   ├── test_sse_stream.py  #   SSE 事件结构验证
│   ├── test_tool_calls.py  #   工具调用事件流测试
│   ├── test_history_api.py #   聊天历史 / todos API 测试
│   ├── test_source_root_imports.py # 源码根导入边界验证
│   └── test_lazy_init.py   #   懒加载 / 单例模式测试
│
├── agent_fronted/          # 前端 Vue 3 项目
│   ├── src/
│   │   ├── views/          #   CustomerService.vue（唯一视图）
│   │   ├── components/     #   ChatMessage / ChatSidebar / TaskPanel / PDFViewer
│   │   ├── services/       #   api.js（前端 API 调用层）
│   │   ├── utils/          #   chatMessageModel / toolEventSummary / chatSessionCache
│   │   └── stores/         #   Pinia 状态管理
│   └── public/
│       ├── images/         #   运行时生成的图表（fig_inter 输出）
│       └── reports/        #   运行时生成的报告（save_report 输出）
│
├── pdfs/                   # 知识库 PDF 源文档
├── faiss_db/               # FAISS 向量索引（运行时生成）
│
├── trash/                  # 统一垃圾站（测试缓存 / pycache / 临时目录）
├── README.md               # 项目说明与开发入口
├── WORKLOG.md              # 工作日志
└── DEPLOY.md               # 部署文档
```

**源码根说明：**

- 后端真实实现现在只保留在 `fault_diagnosis/` 下。
- 运行脚本、测试和导入路径都已切换到 `fault_diagnosis.*`。
- 根目录不再保留 `app.py`、`config.py`、`knowledge_base.py`、`tools/`、`prompts/`、`robot_arm/` 兼容层。
- 结构收敛与退休过程见 [docs/root-layout-refactor.md](docs/root-layout-refactor.md)。

**模块职责划分：**

| 可替换模块 | 说明 |
|-----------|------|
| `fault_diagnosis/tools/` | 领域工具——替换为你的数据查询、API 调用、可视化逻辑 |
| `fault_diagnosis/prompts/` | 系统提示词——替换为你的工作流程和角色设定 |
| `fault_diagnosis/config.py` | 配置常量——修改数据库名、API 地址、Agent 参数 |
| `fault_diagnosis/middleware.py` | 中间件组合——调整摘要策略、上下文管理 |

| 核心模块（通常不改） | 说明 |
|-------------------|------|
| `fault_diagnosis/app.py` | FastAPI 路由、lifespan、Agent 创建 |
| `fault_diagnosis/streaming.py` | SSE token 级流式输出（含心跳保活） |
| `fault_diagnosis/utils.py` | JSON 序列化、todo 解析等通用函数 |
| `fault_diagnosis/logger.py` | 结构化 JSON 日志 + request_id 链路追踪 |
| `fault_diagnosis/session_store.py` | 基于 contextvars 的会话级命名空间隔离 |
| `fault_diagnosis/db_pool.py` | aiomysql 全局异步连接池管理 |
| `fault_diagnosis/dev_mode.py` | 本地开发模式（mock 所有外部依赖） |

## 技术栈

| 层级 | 技术 | 版本 |
|------|------|------|
| Web 框架 | FastAPI + Uvicorn | 0.121.0 / 0.38.0 |
| AI 框架 | LangChain + LangGraph | 1.0.3 / 1.0.5 |
| LLM | OpenAI 兼容 API（ModelScope / 智谱等） | — |
| 向量嵌入 | Ollama (qwen3-embedding:8b) | — |
| 向量检索 | FAISS | — |
| 业务数据库 | MySQL + aiomysql(异步连接池) + SQLAlchemy | — |
| 状态持久化 | PostgreSQL + psycopg | — |
| 可视化 | Matplotlib + Seaborn | — |
| 前端 | Vue 3 + TypeScript + Vite + Element Plus | 3.5 / 7.1 |
| 报告 | ECharts (HTML) + Markdown | — |

## 快速开始

### 1. 环境要求

- Python 3.12+
- Node.js 16+
- MySQL
- PostgreSQL
- （可选）Ollama 服务（知识库向量嵌入，模型 `qwen3-embedding:8b`）

### 2. 后端安装

```bash
git clone https://gitee.com/yxhn05/fault-diagnosis.git
cd fault-diagnosis

# 创建虚拟环境
python3.12 -m venv .venv
source .venv/bin/activate   # Linux/macOS (bash/zsh)
# .venv/bin/python -m fault_diagnosis.app   # 如果 activate 不兼容你的 shell，直接用完整路径

# 安装依赖
pip install -r requirements.txt
```

### 3. 配置环境变量

创建 `.env` 文件：

```env
# LLM 模型配置（OpenAI 兼容接口）
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://api-inference.modelscope.cn/v1
MODEL_NAME=ZhipuAI/GLM-5

# 搜索工具
TAVILY_API_KEY=your_tavily_key

# MySQL（业务数据）
HOST=127.0.0.1
MYSQL_USER=root
MYSQL_PW=your_password
DB_NAME=fault_diagnosis
PORT=3306

# PostgreSQL（对话状态持久化）
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_DB=fault_diagnosis
POSTGRES_USER=your_user
POSTGRES_PASSWORD=your_password

# 运行环境 / 会话
APP_ENV=development
FRONTEND_ORIGINS=http://localhost:9005,http://127.0.0.1:9005,http://localhost:8000,http://127.0.0.1:8000
SESSION_SECRET=replace_with_a_long_random_secret
SESSION_COOKIE_SECURE=false
SESSION_COOKIE_SAMESITE=lax

# 知识库 / 嵌入
OLLAMA_BASE_URL=http://10.108.13.254:11434
EMBEDDING_MODEL=qwen3-embedding:8b
FAISS_PATH=faiss_db
KB_BATCH_SIZE=50
KB_QUERY_TIMEOUT_SECONDS=15
KB_EMBED_TIMEOUT_SECONDS=60
```

`MYSQL_USER` 是项目约定的 MySQL 用户名配置项。不要继续使用通用的 `USER`，否则在某些系统上可能被操作系统环境变量覆盖，导致实际连接数据库时读到错误用户名。

`SESSION_SECRET` 用于签名 `fd_session` cookie、受控 `thread_id` 和旧 thread 映射 cookie。进程环境变量优先于 `.env`；若开发环境未显式配置，默认启动会自动在 `trash/run/session_secret.txt` 生成并复用一份稳定本地 secret，避免“重启即丢会话”。生产环境仍必须显式配置。可用 `python -c "import secrets; print(secrets.token_urlsafe(48))"` 生成固定值。

### 4. 创建数据库

```bash
# PostgreSQL
psql -U your_user -d postgres -c "CREATE DATABASE fault_diagnosis;"

# MySQL
mysql -u root -p -e "CREATE DATABASE fault_diagnosis CHARACTER SET utf8mb4;"
mysql -u root -p -e "CREATE DATABASE dcma CHARACTER SET utf8mb4;"
```

> PostgreSQL 的表结构会在首次启动时由 `checkpointer.setup()` 自动创建。

### 5. 启动服务

```bash
# 后端（终端 1）
.venv/bin/python -m fault_diagnosis.app
# 🚀 服务启动在 http://localhost:8000

# 前端（终端 2）
cd agent_fronted
npm install
npm run dev
# 前端启动在 http://localhost:9005
```

Windows 下可以按两种模式启动：

```powershell
# 真实后端（连接真实依赖）
powershell -ExecutionPolicy Bypass -File .\scripts\run_backend.ps1

# 前端 dev server
powershell -ExecutionPolicy Bypass -File .\scripts\run_frontend_dev.ps1

# 如需无外部依赖的本地开发模式
powershell -ExecutionPolicy Bypass -File .\scripts\run_local_dev.ps1
```

### 日志编码与本地终端

后端默认将 stdout/stderr、控制台日志和 `trash/run/app-json.log` 统一为 UTF-8。JSON 文件日志保留中文原文（`ensure_ascii=False`），便于直接人工查看。

Windows 建议使用 Windows Terminal、PowerShell 7 或 IDE 的 UTF-8 终端查看日志；如果仍使用旧版 `cmd.exe`，请先执行 `chcp 65001`。若外部日志采集器或编辑器按 GBK/ANSI 打开 UTF-8 日志，仍可能显示异常。

打开浏览器访问 `http://localhost:9005`。

### 6. 知识库（可选）

仓库默认自带的 `faiss_db/` 仅是 **2 chunk smoke index**，用途是验证 `query_knowledge_base`、SSE 与工具消费链路是否打通，不能视为正式全量知识库。

将设备手册 PDF 放入 `pdfs/` 目录后，可按以下路径升级：

```bash
# 第一步：先做 smoke 验证（可选）
.venv/bin/python rebuild_kb.py --batch-size 10 --timeout 60 --max-documents 20

# 第二步：确认 smoke 路径正常后，执行 full rebuild
.venv/bin/python rebuild_kb.py --batch-size 10 --timeout 60
```

需要 Ollama 服务运行中（默认 `http://10.108.13.254:11434`，可在 `fault_diagnosis/config.py` 修改）。
先用 `--max-documents` 做小样本验证，确认能落盘和检索后再放大全量。已有索引需要追加新 PDF 时可使用 `--incremental --no-force-rebuild`；构建过程会输出总 chunk 数、当前批次、成功数、失败数、耗时、输出路径和 `build_mode`。健康检查会把当前索引区分为 `smoke` / `full` / `missing`，应用启动时只加载已有索引，缺少 `faiss_db` 时不会在线重建，也不会阻塞主服务。

## 使用场景

### DCMA 运行状态查询

```
用户：了解 DCMA 的运行状态

系统自动执行：
1. sql_db_query → 查询 DCMA 数据库
2. save_html_report → 生成 HTML 报告（KPI 卡片 + ECharts 图表）
```

### 故障码与知识检索

```
用户：查询故障代码 F01002 的含义和处理步骤

系统自动执行：
1. query_knowledge_base → 查询本地手册
2. search_tool（如有必要）→ 补充外部标准或厂商建议
3. save_report / save_html_report → 输出结果
```

### 可选机械臂模块

- 机械臂能力已迁入 `fault_diagnosis/robot_arm/`
- 默认关闭，需在 `.env` 中设置 `ENABLE_ROBOT_ARM=true`
- 启用后才会把 `sql_inter`、`extract_data`、`fig_inter`、`fault_explanation_tool` 注册到主 Agent

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/chat/stream` | GET | SSE 流式聊天（message, thread_id, user_identity） |
| `/ai/history/{type}` | GET | 获取对话列表 |
| `/ai/history/{type}/{chat_id}` | GET | 获取对话消息历史 |
| `/api/todos/{thread_id}` | GET | 获取任务清单 |
| `/health/dependencies` | GET | 不触发 LLM 推理的真实依赖健康检查 |
| `/health/real` | GET | 真实联调健康检查别名 |
| `/images/*` | Static | 生成的图表文件 |
| `/reports/*` | Static | 生成的报告文件 |

## 会话说明

- 当前后端会为浏览器签发 `fd_session` cookie，并只接受当前服务端会话拥有的签名 `thread_id`
- 历史接口现在会把 LangChain 风格角色统一规范为 `user/assistant/tool/system`，与前端实时消息协议保持一致
- 旧格式 `thread_id` 不会被直接恢复为可读历史；同浏览器中若已有本地缓存，会以前端“本地缓存只读”形式显示
- 同浏览器刷新历史时，前端会优先用本地缓存修补服务端已退化的旧 user 文本，降低刷新后说话人混乱和乱码风险
- 用户从本地缓存历史继续提问时，系统会自动切换到新的受控 `thread_id`，但旧缓存内容不会自动回灌到服务端上下文
- 若 `SESSION_SECRET` 未固定配置，开发环境会退化为进程级临时密钥并在启动日志输出 warning；服务重启后旧 cookie / 旧 thread 映射会失效
- 历史消息和 todo 状态由 PostgreSQL checkpointer 持久化；浏览器是否还能访问这些历史，取决于固定 `SESSION_SECRET` 能否继续验签旧 cookie 和 `thread_id`
- 仍需注意：跨浏览器或无本地缓存访问旧会话时，如果服务端历史里已经持久化了退化文本，前端无法完全恢复原始 user 内容

## 生产部署

```bash
# 构建前端
cd agent_fronted && npm run build

# 启动后端（4 worker）
gunicorn -w 4 -k uvicorn.workers.UvicornWorker fault_diagnosis.app:app --bind 0.0.0.0:8000
```

生产环境建议至少补齐以下配置：

- `APP_ENV=production`
- `SESSION_SECRET=<长随机字符串>`
- `FRONTEND_ORIGINS=<实际前端来源列表>`
- `SESSION_COOKIE_SECURE=true`
- 若前后端跨站点部署，再根据浏览器策略评估 `SESSION_COOKIE_SAMESITE=None`，同时必须保持 `SESSION_COOKIE_SECURE=true`

## 仓库整理说明

- `pytest.ini` 是测试配置文件，需要保留在仓库根目录。
- `.pytest_cache/`、`pytest-cache-files-*`、`__pycache__/`、`*.log`、`run_state*.txt`、`agent_fronted/dist/` 都属于运行或构建产物，不纳入源码结构。
- 现在这些测试缓存和临时产物优先统一收敛到 [`trash/`](D:/code/fault-diagnosis-master/trash)；测试脚本已默认把 pytest 临时目录、cache 和 pycache 重定向到这里。
- 需要集中清理时可运行 [`scripts/clean_garbage.ps1`](D:/code/fault-diagnosis-master/scripts/clean_garbage.ps1)。
- 启动脚本已统一收拢到 `scripts/`，报告模板已收拢到 `templates/`，辅助分析文档放入 `docs/`。

## 自定义（Fork 后搭建你的 Agent）

Fork 本项目后，替换以下 4 个模块即可搭建你自己领域的 Agent 服务：

### 1. 替换工具 (`fault_diagnosis/tools/`)

```python
# fault_diagnosis/tools/your_tool.py
from pydantic import BaseModel, Field
from langchain_core.tools import tool

class MyToolInput(BaseModel):
    query: str = Field(description="查询内容")

@tool(args_schema=MyToolInput)
def my_tool(query: str) -> str:
    """工具描述（中文，作为 LLM 的工具说明）"""
    return "结果"
```

在 `fault_diagnosis/tools/__init__.py` 中注册：
```python
from .your_tool import my_tool
tools = [my_tool, ...]
```

### 2. 替换提示词 (`fault_diagnosis/prompts/`)

修改 `fault_diagnosis/prompts/system_prompt.py` 中的 `systemprompt` 字符串，定义你的角色和工作流程。

### 3. 修改配置 (`fault_diagnosis/config.py`)

调整数据库名、API 地址、Agent 参数等。

### 4. 调整中间件 (`fault_diagnosis/middleware.py`)

根据需要启用/禁用 TodoList、Summarization 等中间件。

**核心部分**（`fault_diagnosis/app.py`、`fault_diagnosis/streaming.py`、`fault_diagnosis/utils.py`）通常不需要修改。

## 后续开发方向

### 近期目标

- **前端重构**：Vue 3 前端代码整理，组件化、状态管理优化
- **多模型支持**：前端可切换不同 LLM 模型，对比诊断效果
- **Docker 部署**：提供 docker-compose 一键启动（后端 + MySQL + PostgreSQL + Ollama）

### 中期目标

- **多设备支持**：扩展传感器数据模型，支持不同类型工业设备接入
- **实时监控**：WebSocket 推送设备实时状态，异常自动触发诊断
- **报告模板系统**：可视化配置报告布局，支持自定义 KPI 和图表类型
- **权限管理**：完善用户角色体系，支持团队协作和审批流程

### 长期愿景

- **多 Agent 协作**：设备诊断 Agent、维护调度 Agent、备件管理 Agent 协同工作
- **预测性维护**：基于历史数据训练故障预测模型，从被动诊断转向主动预防
- **知识图谱**：构建设备-故障-维修知识图谱，提升诊断的关联推理能力
- **移动端**：提供移动端 App，支持现场诊断和离线报告查看
