# fault_diagnosis 后端说明

`fault_diagnosis/` 是项目唯一后端源码根，当前主链路是限制型单 Agent：请求理解、受限 SQL、知识库检索、诊断分析、可选可视化 HTML 报告、最终回答。后端不再维护多 agent、多流程分流或在线质量评估链路。

## 启动入口

开发启动：

```bash
python -m fault_diagnosis.app
```

生产启动：

```bash
gunicorn -w 4 -k uvicorn.workers.UvicornWorker fault_diagnosis.app:app --bind 0.0.0.0:8000
```

## 分层结构

```text
fault_diagnosis/
  app.py                 进程入口
  app_factory.py         FastAPI app 组装
  config.py              环境变量与集中配置
  api/                   HTTP/SSE 路由层
  services/              应用服务层
  auth/                  session、管理员身份、thread 归属
  infrastructure/        CORS、lifespan、模型、静态资源、数据库池
  agent_runtime/         SSE 编码、流调度、取消控制、错误分类
  single_agent/          单 agent 编排、阶段处理、prompt、策略与序列化 helper
  diagnosis/             诊断域合同、step helper、artifact store
  tools/                 SQL、知识库、HTML 报告工具
  knowledge/             FAISS/Ollama 知识库与上传 PDF 知识库
  repositories/          历史索引、PDF registry、治理文件仓储
  runtime/               dev mode、请求 namespace、前端兼容契约适配
  common/                日志、路径、编码、通用工具
  integrations/          OCR 等外部集成
```

## 请求链路

`GET /chat/stream` 调用链：

```text
api/chat.py
  -> ChatService.stream_chat
  -> agent_runtime.streaming.token_stream_events
  -> single_agent.RestrictedSingleAgentRunner
```

`POST /agent/chat` 是语音网关兼容接口，不复制 agent 逻辑，而是内部消费同一条 SSE 流并聚合为 JSON。

## 单 Agent 流程

```text
understand
  -> sql（按 decision 可跳过）
  -> knowledge（按 decision 可跳过）
  -> analysis
  -> report（按 decision 可跳过）
  -> final_answer
  -> save diagnosis artifact
```

工具白名单：

- `sql_db_query_checker`
- `sql_db_query`
- `query_knowledge_base`
- `save_report`

SQL 阶段只允许访问 `real_data_01`、`real_data_02`、`real_data_03`、`device_alarm`、`device_metric`、`device_fault_data`、`fault_records`，并只允许只读 `SELECT/WITH` 查询。最近/当前运行状态默认查询 `real_data_01`，未知表、旧表 `real_data` 或非只读 SQL 会回退到受限最近数据查询。

`single_agent/` 内部按职责拆分：

- `runner.py`：对外入口、运行状态、模型调用、工具调用白名单与错误封装。
- `flow.py`：SSE 状态机与阶段编排顺序。
- `stages.py`：understand、SQL、knowledge、analysis、report、final answer 阶段实现。
- `intent.py`、`sql_safety.py`、`reporting.py`、`artifacts.py`：可单测的业务 helper。
- `serialization.py`、`json_utils.py`、`tool_access.py`：通用序列化、JSON 修复与工具懒加载。

## 诊断产物

`diagnosis/` 保存单 agent 复用的领域能力：

- `contracts.py`：请求、证据项、SQL/知识库/分析/报告 artifact、线程级 `DiagnosisArtifactEnvelope`。
- `steps/`：请求 payload 解析、SQL 计划、知识库查询文本与结果归一化。
- `artifact_store.py`：线程级诊断产物存储 facade。
- `artifact_backends/`：file、memory、postgres 三种后端。
- `report_mapper.py`：把最近一次诊断产物映射成报告生成输入。

默认 artifact 后端是文件：

```text
trash/run/diagnosis_artifacts/*.jsonl
```

可通过环境变量切换：

```env
DIAGNOSIS_ARTIFACT_BACKEND=file|memory|postgres
DIAGNOSIS_ARTIFACT_DIR=trash/run/diagnosis_artifacts
DIAGNOSIS_ARTIFACT_TABLE=diagnosis_artifacts
DIAGNOSIS_ARTIFACT_POSTGRES_DSN=postgresql://...
```

旧 `WORKFLOW_ARTIFACT_*` 环境变量仍会作为兼容 fallback 读取，但新配置应使用 `DIAGNOSIS_ARTIFACT_*`。

## SSE 契约

当前事件序列：

```text
start -> ping* -> tool_start/tool_end* -> token -> complete
```

`complete` 会包含 `decision`、各阶段 artifact、`trace`、线程级 `artifact`，并补充 `workflow_result`、`workflow_envelope` 等前端兼容字段。字段名保留只是为了兼容前端，不代表后端仍有独立 workflow runner。

详细字段见 [docs/sse-event-contract.md](../docs/sse-event-contract.md)。

## 运行态依赖

- MySQL：SQL 工具和依赖健康检查。
- OpenAI 兼容 LLM：请求理解、分析、最终回答。
- Ollama + FAISS：本地 PDF 知识库检索。
- PostgreSQL：可选诊断 artifact backend 和健康检查。
- 本地文件系统：报告、上传 PDF registry、治理快照、历史索引。

`LOCAL_DEV_MODE=true` 时会跳过外部依赖初始化，使用 `runtime/dev_mode.py` 的模拟 SSE。

## 扩展约定

1. 新 HTTP 能力放在 `api/`，用例编排放在 `services/`。
2. 新持久化能力放在 `repositories/`，不要在路由里直接写文件。
3. 新单 agent 工具必须显式加入 `RestrictedSingleAgentRunner` 的白名单和对应阶段。
4. 修改诊断流程顺序优先改 `single_agent/flow.py`，修改单个阶段优先改 `single_agent/stages.py`，不要把阶段细节重新塞回 `runner.py`。
5. 外部依赖健康检查只保留当前运行链路会使用的依赖。
