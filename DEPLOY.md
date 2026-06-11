# 内网服务器部署方案

本文档用于部署当前限制型单 Agent 故障诊断系统。后端入口统一为 `fault_diagnosis.app:app`，知识库重建统一使用 `rebuild_kb.py`。

## 1. 环境准备

建议版本：

- Python 3.12
- Node.js 16+
- MySQL
- 可选 PostgreSQL
- 可选 Ollama，知识库向量检索需要

检查命令：

```bash
python --version
node --version
npm --version
mysql --version
psql --version
curl http://<ollama-host>:11434/api/tags
```

## 2. 项目目录

示例部署到 `/opt/agent`：

```bash
mkdir -p /opt/agent
cd /opt/agent
```

需要复制：

```text
fault_diagnosis/      后端源码
agent_fronted/        前端源码
medicineOCR/          OCR 辅助脚本
pdfs/                 知识库 PDF
faiss_db/             可选，已有 FAISS 索引
docs/                 契约文档
rebuild_kb.py
requirements.txt
pytest.ini
DEPLOY.md
README.md
.env                  生产环境变量
```

## 3. 后端部署

```bash
cd /opt/agent
conda create -n faultagent python=3.12.10 -y
conda activate faultagent
pip install -r requirements.txt
```

`.env` 示例：

```env
APP_ENV=production

OPENAI_API_KEY=replace_me
OPENAI_BASE_URL=http://your-llm-gateway/v1
MODEL_NAME=your-model

HOST=10.108.12.164
MYSQL_USER=root
MYSQL_PW=707707
DB_NAME=agent
PORT=3306
DCMA_DB_NAME=dcma

OLLAMA_BASE_URL=http://10.108.13.254:11434
EMBEDDING_MODEL=qwen3-embedding:8b
FAISS_PATH=faiss_db

FRONTEND_ORIGINS=http://your-frontend-host
SESSION_SECRET=replace_with_a_long_random_secret
SESSION_COOKIE_SECURE=true
SESSION_COOKIE_SAMESITE=lax
SESSION_COOKIE_DOMAIN=
SESSION_COOKIE_PATH=/

DIAGNOSIS_ARTIFACT_BACKEND=file

# 可选：Langfuse trace 导出
AGENT_TRACE_BACKEND=langfuse
LANGFUSE_PUBLIC_KEY=replace_me
LANGFUSE_SECRET_KEY=replace_me
LANGFUSE_HOST=https://cloud.langfuse.com
AGENT_TRACE_CAPTURE_CONTENT=false
AGENT_TRACE_FLUSH_ON_RUN=false
AGENT_TRACE_LOCAL_LOG=false
AGENT_TRACE_LOCAL_LOG_PATH=trash/run/agent-trace.jsonl
```

生产环境缺少 `SESSION_SECRET` 时服务会拒绝启动。生成示例：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

跨站点携带 cookie 时，设置：

```env
SESSION_COOKIE_SAMESITE=none
SESSION_COOKIE_SECURE=true
```

## 4. 知识库初始化

确认 PDF：

```bash
ls -la pdfs/
```

先做小样本验证：

```bash
python rebuild_kb.py --batch-size 10 --timeout 60 --max-documents 20
```

再构建完整索引：

```bash
python rebuild_kb.py --batch-size 10 --timeout 60
```

增量追加：

```bash
python rebuild_kb.py --incremental --no-force-rebuild
```

## 5. 前端构建

```bash
cd /opt/agent/agent_fronted
npm install
npm run build
```

构建产物默认由后端静态挂载读取。

## 6. 启动后端

开发/联调：

```bash
cd /opt/agent
conda activate faultagent
python -m fault_diagnosis.app
```

生产：

```bash
gunicorn -w 4 -k uvicorn.workers.UvicornWorker fault_diagnosis.app:app --bind 0.0.0.0:8000
```

后台运行：

```bash
nohup conda run -n faultagent gunicorn -w 4 -k uvicorn.workers.UvicornWorker fault_diagnosis.app:app --bind 0.0.0.0:8000 > app.log 2>&1 &
```

## 7. 健康检查

```bash
curl http://127.0.0.1:8000/health/dependencies?deep=false
curl http://127.0.0.1:8000/health/dependencies?deep=true
```

`deep=false` 不触发 LLM 推理，适合启动后快速检查。`deep=true` 会检查更真实的依赖连通性。

## 8. Nginx SSE 配置

关键点是关闭代理缓冲并拉长超时：

```nginx
location /chat/stream {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 3600s;
}

location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
```

## 9. 运行态目录

```text
agent_fronted/public/reports/      Markdown 报告
agent_fronted/public/images/       静态图片目录
trash/run/diagnosis_artifacts/     线程级诊断产物
trash/run/admin_uploads/           管理员上传 PDF
trash/run/app-json.log             JSON 日志
```

## 10. 常见问题

- 启动失败并提示 `SESSION_SECRET`：生产环境必须显式配置固定随机值。
- 知识库不可用：确认 `faiss_db/` 存在，Ollama 可访问，`EMBEDDING_MODEL` 已下载。
- SQL 工具不可用：确认 `HOST`、`MYSQL_USER`、`MYSQL_PW`、`PORT`、`DCMA_DB_NAME`。
- SSE 无输出：确认 Nginx `proxy_buffering off`，并查看 `trash/run/app-json.log`。
- Langfuse 没有 trace：确认 `AGENT_TRACE_BACKEND=langfuse`，并已配置 `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`。
- 需要本地查看完整 trace：设置 `AGENT_TRACE_LOCAL_LOG=true` 后重启服务，再查看 `trash/run/agent-trace.jsonl`。
