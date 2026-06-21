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
  auth/                  session、签名身份 cookie、thread 归属
  security/              权限合同、RBAC/ABAC、SQL/RAG ACL、工具网关与审计
  infrastructure/        CORS、lifespan、模型、静态资源、数据库池
  agent_runtime/         SSE 编码、流调度、取消控制、错误分类
  single_agent/          单 agent 编排、阶段处理、workflow、输出、证据链
  diagnosis/             诊断域合同、step helper、artifact store
  tools/                 SQL、知识库、HTML 报告工具
  knowledge/             FAISS/Ollama 知识库与上传 PDF 知识库
  repositories/          历史索引、PDF registry、治理文件仓储
  runtime/               dev mode、请求 namespace、前端兼容契约适配
  common/                日志、路径、编码、通用工具
  integrations/          OCR 等外部集成
```

按企业分层归属：

- 接口层：`api/`，只处理 HTTP/SSE 入参、响应和状态码。
- 应用层：`services/`，编排用户用例，负责会话、权限、历史、停止流等应用动作。
- Agent 层：`agent_runtime/` 和 `single_agent/`。前者是流式协议、取消、错误分类等运行时适配；后者是单 Agent 的业务编排、workflow policy、阶段实现、输出契约和证据链。
- 诊断领域层：`diagnosis/`，保存请求、证据、artifact 等领域合同和 artifact store；这些不是 agent 私有实现，后续多入口也可以复用。
- 能力与外部资源层：`tools/`、`knowledge/`、`integrations/`，分别承载工具封装、知识库索引/检索、OCR 等外部集成。
- 基础设施层：`infrastructure/`、`repositories/`、`observability/`、`auth/`、`runtime/`、`common/`，分别承载启动、持久化、trace、身份、运行态兼容和通用能力。

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
  -> access_authorization
  -> select_workflow_policy
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
- `workflow/`：任务分类、workflow policy、节点开关和任务清单。
- `output/`：`complete` 事件与前端兼容输出字段构建，后续输出模板优先从这里扩展。
- `evidence/`：EvidenceBundle 门面、SQL/知识库来源证据、Claim 和质量校验。
- `support/`：序列化、JSON 修复、工具懒加载等 agent 内部支撑能力。
- `workorder_suggestions.py`：把诊断产物转换为工单草稿建议，`reporting.py` 仅保留兼容入口。
- `intent.py`、`sql_safety.py`、`reporting.py`、`artifacts.py`：可单测的业务 helper。

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
4. 修改诊断流程顺序优先改 `single_agent/flow.py`，修改单个阶段优先改 `single_agent/stages.py`，修改最终输出字段优先改 `single_agent/output/`，不要把阶段细节重新塞回 `runner.py`。
5. 外部依赖健康检查只保留当前运行链路会使用的依赖。

## 身份与权限（RBAC + ABAC）

权限实现位于 `fault_diagnosis/security/`，身份、workflow、工具、SQL、知识库、工单和报告访问分别校验，前端传入的 `user_identity` 不参与授权。

接口：

- `POST /auth/login`：工程师/文件用户登录。
- `POST /auth/admin/login`：兼容现有管理员登录。
- `POST /auth/logout`：清理普通用户与管理员 cookie。
- `GET /auth/identity`：返回兼容身份字段以及 `role`、`permissions` 和资源范围。

普通用户默认从 `trash/run/users.json` 读取，也可通过 `USER_STORE_PATH` 指定。密码只接受 PBKDF2-SHA256 哈希，不接受明文。可用以下方式生成哈希：

```bash
python -c "from getpass import getpass; from fault_diagnosis.repositories.user_repository import hash_password; print(hash_password(getpass()))"
```

用户文件示例：

```json
[
  {
    "user_id": "engineer_01",
    "username": "engineer_01",
    "password_hash": "pbkdf2_sha256$600000$...$...",
    "role": "engineer",
    "display_name": "维修工程师01",
    "asset_scope": ["J1号机", "pump_001"],
    "table_scope": ["real_data_01", "device_alarm", "device_metric"],
    "system_scope": ["DCMA_LINE_1"],
    "enabled": true
  }
]
```

部署时应将用户文件权限设为仅服务账号可读，并显式配置稳定的 `SESSION_SECRET`。安全审计默认写入 `trash/run/security-audit.jsonl`，可用 `SECURITY_AUDIT_PATH` 调整。

诊断报告现写入私有的 `trash/run/reports/`，通过受保护的 `/reports/{filename}` 返回；旧的公共静态报告目录不再挂载。工程师只能查看当前设备/数据表范围内的报告，管理员可查看全部报告。
