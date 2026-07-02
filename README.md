# 工业设备故障诊断专家系统

这是一个面向 DCMA 工业设备的故障诊断 Agent 项目。当前后端已经收敛为限制型单 Agent 主链路：受控地查询设备数据、检索 PDF 知识库、形成诊断结论，并按需生成可视化 HTML 报告。

## 当前架构

```text
Vue 3 frontend
  -> FastAPI HTTP/SSE
    -> services.chat
      -> agent_runtime.streaming
        -> single_agent.RestrictedSingleAgentRunner
          -> SQL / knowledge base / report tools
```

后端源码只保留在 `fault_diagnosis/` 下，不再维护根目录 shim、多 agent、多流程分流或在线质量评估链路。

## 核心能力

- 单 Agent 故障诊断：请求理解、受限 SQL、知识库检索、诊断分析、最终回答。
- 受限 SQL：只允许白名单表和只读查询，异常 SQL 自动回退到安全查询。
- PDF 知识库：基于 FAISS/Ollama embeddings 查询设备手册和故障码资料。
- 可视化 HTML 报告：通过 `save_report` 保存到私有报告目录，并经受保护的 `/reports/{filename}` 访问。
- 会话隔离：服务端 session cookie 管理 thread 归属，忽略不可信的前端身份参数。
- 管理员 PDF：支持上传 PDF、OCR/解析、校正、归档到上传知识库。

## 目录结构

```text
.
├── fault_diagnosis/        # 后端源码根
│   ├── app.py              # 后端入口
│   ├── app_factory.py      # FastAPI app 组装
│   ├── api/                # HTTP/SSE 路由
│   ├── services/           # 应用服务
│   ├── single_agent/       # 限制型单 Agent
│   ├── agent_runtime/      # SSE、流控、错误分类
│   ├── diagnosis/          # 诊断合同、step、artifact store
│   ├── tools/              # SQL、知识库、HTML 报告工具
│   ├── knowledge/          # FAISS/Ollama 知识库
│   ├── repositories/       # 文件/索引仓储
│   └── infrastructure/     # lifespan、CORS、模型、数据库池
├── agent_fronted/          # Vue 3 前端
├── medicineOCR/            # OCR 辅助脚本与独立依赖
├── pdfs/                   # 知识库 PDF 源文档
├── faiss_db/               # FAISS 索引
├── docs/                   # 当前态架构与 API/SSE 契约
├── rebuild_kb.py           # 知识库重建入口
├── requirements.txt        # 后端依赖
└── DEPLOY.md               # 部署说明
```

后端详细说明见 [fault_diagnosis/README.md](fault_diagnosis/README.md)。

## 快速开始

### 后端

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m fault_diagnosis.app
```

生产启动：

```bash
gunicorn -w 4 -k uvicorn.workers.UvicornWorker fault_diagnosis.app:app --bind 0.0.0.0:8000
```

### 前端

```bash
cd agent_fronted
npm install
npm run dev
```

默认前端开发地址：`http://localhost:9005`。后端默认地址：`http://localhost:8000`。

## 环境变量

最小示例：

```env
APP_ENV=development

OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://your-openai-compatible-endpoint/v1
MODEL_NAME=your-model

HOST=127.0.0.1
MYSQL_USER=root
MYSQL_PW=your_password
PORT=3306
DCMA_DB_NAME=dcma

OLLAMA_BASE_URL=http://127.0.0.1:11434
EMBEDDING_MODEL=qwen3-embedding:8b
FAISS_PATH=faiss_db

FRONTEND_ORIGINS=http://localhost:9005,http://127.0.0.1:9005
SESSION_SECRET=replace_with_a_long_random_secret
SESSION_COOKIE_SECURE=false
SESSION_COOKIE_SAMESITE=lax
```

生产环境必须显式配置 `SESSION_SECRET`。可用以下命令生成：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

诊断产物存储可选配置：

```env
DIAGNOSIS_ARTIFACT_BACKEND=file
DIAGNOSIS_ARTIFACT_TABLE=diagnosis_artifacts
DIAGNOSIS_ARTIFACT_POSTGRES_DSN=

# 可选：Langfuse trace 导出
AGENT_TRACE_BACKEND=langfuse
LANGFUSE_PUBLIC_KEY=replace_me
LANGFUSE_SECRET_KEY=replace_me
LANGFUSE_HOST=https://cloud.langfuse.com
AGENT_TRACE_CAPTURE_CONTENT=false
AGENT_TRACE_FLUSH_ON_RUN=false
AGENT_TRACE_LOCAL_LOG=false
AGENT_TRACE_LOCAL_LOG_PATH=trash/run/agent-trace.jsonl
AGENT_TRACE_CONSOLE=false
AGENT_TRACE_CONSOLE_VERBOSE=false
AGENT_TRACE_CONSOLE_PREVIEW_CHARS=240
```

## 知识库

仓库自带的 `faiss_db/` 仅用于 smoke 验证，不代表正式知识库。放入正式 PDF 后执行：

```bash
python rebuild_kb.py --batch-size 10 --timeout 60 --max-documents 20
python rebuild_kb.py --batch-size 10 --timeout 60
```

增量追加：

```bash
python rebuild_kb.py --incremental --no-force-rebuild
```

## 主要 API

| 路径 | 方法 | 说明 |
| --- | --- | --- |
| `/chat/stream` | GET | SSE 流式聊天 |
| `/chat/stream/edit` | GET | 编辑指定用户轮次后重新生成 |
| `/chat/stop` | POST | 停止当前会话中的活跃流 |
| `/agent/chat` | POST | 语音网关兼容 JSON 接口 |
| `/ai/history/{type}` | GET | 当前 session 历史列表 |
| `/admin/pdfs` | GET/POST | 管理员 PDF 列表/上传 |
| `/health/dependencies` | GET | 依赖健康检查 |
| `/reports/{filename}` | GET | 受保护的报告访问 |

文档：

- [Docs index](docs/README.md)
- [Current architecture](docs/current-architecture.md)
- [HTTP API](docs/backend-api-contract.md)
- [SSE events](docs/sse-event-contract.md)

默认仅保留内存态 `AgentTrace` 和 SSE `trace_id`；开启 Langfuse 后会导出外部 trace。需要本地排障文件时，可设置 `AGENT_TRACE_LOCAL_LOG=true`，请求结束后会追加写入 `trash/run/agent-trace.jsonl`。开发阶段想直接在后端终端看阶段、模型调用和工具调用摘要时，可设置 `AGENT_TRACE_CONSOLE=true`；默认是简洁模式，如需在终端展示 artifact、工具入参和结果预览，再设置 `AGENT_TRACE_CONSOLE_VERBOSE=true`。

## 本地开发模式

不想连接 MySQL、LLM、Ollama 时可启用：

```env
LOCAL_DEV_MODE=true
```

该模式使用 `fault_diagnosis/runtime/dev_mode.py` 的模拟 SSE 和本地状态；模拟流同样经过正式的 workflow 权限策略，并在 `complete.authorization` 中返回授权结果。

本地权限验收可直接运行：

```bash
LOCAL_DEV_MODE=true python -m uvicorn fault_diagnosis.app:app --host 127.0.0.1 --port 8000
scripts/auth_acceptance_test.sh
```

如需在非 `LOCAL_DEV_MODE` 的开发进程中单独开放开发身份接口，可设置 `ENABLE_DEV_AUTH=true`。`POST /auth/dev-login` 仅在这两个开关之一启用且 `APP_ENV` 不是生产环境时可用；生产环境固定返回 404。
