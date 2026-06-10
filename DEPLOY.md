# 内网服务器部署方案

> 本文档用于指导在内网服务器上快速部署本项目的完整流程

## 📋 部署前准备

### 1. 确认服务器环境

```bash
# 检查 Python 版本 (需要 3.10+)
python --version

# 检查 Node.js 版本 (需要 16+)
node --version
npm --version

# 检查 MySQL
mysql --version

# 检查 PostgreSQL
psql --version
```

### 2. 确认网络连通性

| 服务       | 地址                | 检查命令                                     |
| ---------- | ------------------- | -------------------------------------------- |
| MySQL      | 10.108.12.164:3306  | `telnet 10.108.12.164 3306`                |
| PostgreSQL | 10.108.13.254:5434  | `telnet 10.108.13.254 5434`                |
| Ollama     | 10.108.13.254:11434 | `curl http://10.108.13.254:11434/api/tags` |

### 3. 确认 Ollama 模型

```bash
# 在 Ollama 服务器上执行，确认所需模型已下载
ollama list

# 需要以下模型：
# - glm-4.7-flash-46k:latest
# - qwen3-embedding:8b
```

---

## 🚀 快速部署步骤

### 第一步：项目部署

```bash
# 1. 将项目复制到服务器（通过 SCP、U盘等方式）
# 假设部署目录为 /opt/agent
mkdir -p /opt/agent
cd /opt/agent

# 2. 复制项目文件到该目录
# - 后端入口与脚本：rebuild_kb.py, requirements.txt, scripts/ 等
# - 后端主源码：fault_diagnosis/ 目录
# - 报告模板：templates/ 目录
# - 前端代码：agent_fronted/ 目录
# - PDF文档：pdfs/ 目录
# - 环境配置：.env 文件
```

### 第二步：后端部署

```bash
cd /opt/agent

# 1. 创建 Python 虚拟环境
conda create -n faultagent python=3.12.10 -y

# 2. 激活虚拟环境
conda activate faultagent

# 3. 安装依赖
pip install -r requirements.txt

# 4. 验证安装
python --version  # 应显示 3.12.x

# 5. 检查 .env 文件配置
cat .env
```

**环境变量配置示例 (.env)**：

```env
# 运行环境（生产环境必须显式配置）
APP_ENV=production

# MySQL 数据库配置
HOST=10.108.12.164
USER=root
MYSQL_PW=707707
DB_NAME=agent
PORT=3306

# PostgreSQL 配置（用于 LangGraph 状态持久化）
POSTGRES_HOST=10.108.13.254
POSTGRES_PORT=5434
POSTGRES_DB=agent
POSTGRES_USER=agent
POSTGRES_PASSWORD=707707

# Ollama 本地模型服务
OLLAMA_BASE_URL=http://10.108.13.254:11434

# SSE / 会话
FRONTEND_ORIGINS=http://your-frontend-host
SESSION_SECRET=replace_with_a_long_random_secret
SESSION_COOKIE_SECURE=true
SESSION_COOKIE_SAMESITE=lax
SESSION_COOKIE_DOMAIN=
SESSION_COOKIE_PATH=/
```

> 说明：
> - `APP_ENV=production` 且缺少 `SESSION_SECRET` 时，服务会拒绝启动，避免进入“重启即失效”的危险部署状态。
> - `FRONTEND_ORIGINS` 未配置时，生产环境只建议同源部署；若前后端分域，必须显式填写来源。
> - 若浏览器必须跨站点携带 SSE cookie，请将 `SESSION_COOKIE_SAMESITE=None`，同时保持 `SESSION_COOKIE_SECURE=true`。

### 第三步：初始化知识库

```bash
# 确保 pdfs/ 目录有 PDF 文件
ls -la pdfs/

# 启动 Python 环境并初始化知识库
python -c "from fault_diagnosis.knowledge_base import init_knowledge_base; init_knowledge_base()"

# 检查是否生成 faiss_db/ 目录
ls -la faiss_db/
```

### 第四步：前端构建

```bash
cd /opt/agent/agent_fronted

# 1. 安装依赖
npm install

# 2. 构建生产版本
npm run build

# 3. 检查构建输出
ls -la dist/
```

### 第五步：启动服务

```bash
cd /opt/agent

# 确保在 Conda 环境中
conda activate faultagent

# 方式1：直接启动（开发测试）
python -m fault_diagnosis.app

# 方式2：使用 gunicorn 生产部署（推荐）
gunicorn -w 4 -k uvicorn.workers.UvicornWorker fault_diagnosis.app:app --bind 0.0.0.0:8000

# 方式3：后台运行（Linux）
nohup gunicorn -w 4 -k uvicorn.workers.UvicornWorker fault_diagnosis.app:app --bind 0.0.0.0:8000 > app.log 2>&1 &

# 方式4：使用 conda run 后台运行（无需手动激活环境）
nohup conda run -n faultagent gunicorn -w 4 -k uvicorn.workers.UvicornWorker fault_diagnosis.app:app --bind 0.0.0.0:8000 > app.log 2>&1 &
```

**日志编码说明**：

- 后端入口会在进程内默认设置 `PYTHONIOENCODING=utf-8`、`PYTHONUTF8=1`，并尽量将 stdout/stderr 重新配置为 UTF-8。
- `trash/run/app-json.log` 使用 UTF-8 写入，JSON 日志保留中文原文（`ensure_ascii=False`），不是 `\uXXXX` 转义。
- Linux/macOS 服务器建议保持 `LANG=C.UTF-8` 或其它 UTF-8 locale；Windows 本地联调建议使用 Windows Terminal / PowerShell 7。旧版 `cmd.exe` 可先执行 `chcp 65001`。
- 外部日志采集器、编辑器或终端仍需按 UTF-8 解码日志；如果显示端强制使用 GBK/ANSI，项目侧输出正确也可能看起来异常。

---

## 📁 目录结构说明

部署后的目录结构：

```
/opt/agent/
├── rebuild_kb.py               # 知识库重建入口
├── requirements.txt            # Python 依赖
├── .env                        # 环境变量配置
├── fault_diagnosis/            # 后端主源码根
│   ├── app.py
│   ├── config.py
│   ├── tools/
│   ├── prompts/
│   └── robot_arm/
├── templates/                  # 报告模板
│   ├── html_template.html
│   └── md_template.md
├── faiss_db/                   # 向量数据库（自动生成）
│   ├── index.faiss
│   └── index.pkl
├── pdfs/                       # PDF 知识库文档
├── scripts/                    # 启动 / 测试辅助脚本
├── agent_fronted/              # 前端项目
│   ├── dist/                   # 构建输出（npm run build 生成）
│   └── public/                 # 静态文件
│       ├── images/             # 生成的图表
│       └── reports/            # 生成的报告
└── app.log                     # 运行日志
```

> 说明：
> - 生产 / 开发入口已统一到 `fault_diagnosis/`：`python -m fault_diagnosis.app`、`gunicorn ... fault_diagnosis.app:app`。
> - `fault_diagnosis/` 是唯一后端源码根；根目录不再保留同名 shim 模块和 `tools/` / `prompts/` / `robot_arm/` 兼容包。

---

## ⚙️ Nginx 配置（推荐）

如果使用 Nginx 作为反向代理：

```nginx
server {
    listen 80;
    server_name your-server-ip;

    # 前端静态文件
    location / {
        root /opt/agent/agent_fronted/dist;
        index index.html;
        try_files $uri $uri/ /index.html;
    }

    # 后端 API
    location /api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    # SSE 流式接口需要特殊配置
    location /chat/stream {
        proxy_pass http://127.0.0.1:8000/chat/stream;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
    }

    # 图片和报告静态文件
    location /images/ {
        alias /opt/agent/agent_fronted/public/images/;
    }

    location /reports/ {
        alias /opt/agent/agent_fronted/public/reports/;
    }
}
```

---

## 🔧 常见问题排查

### 1. 数据库连接失败

```bash
# 测试 MySQL 连接
mysql -h 10.108.12.164 -P 3306 -u root -p

# 测试 PostgreSQL 连接
psql -h 10.108.13.254 -p 5434 -U agent -d agent
```

### 2. Ollama 连接失败

```bash
# 测试 Ollama 服务
curl http://10.108.13.254:11434/api/tags

# 如果失败，检查 Ollama 是否运行
# 在 Ollama 服务器上执行：
ollama serve
```

### 3. Conda 环境问题

```bash
# 如果提示 conda 命令不存在，需要先初始化 conda
source ~/anaconda3/etc/profile.d/conda.sh
# 或
source ~/miniconda3/etc/profile.d/conda.sh

# 然后激活环境
conda activate faultagent
```

### 4. 权限问题

```bash
# 确保有写入权限
chmod -R 755 /opt/agent/agent_fronted/public/images
chmod -R 755 /opt/agent/agent_fronted/public/reports
chmod -R 755 /opt/agent/faiss_db
```

### 5. 端口占用

```bash
# 检查 8000 端口是否被占用
netstat -tlnp | grep 8000

# 如果被占用，更换端口启动
conda activate faultagent
uvicorn fault_diagnosis.app:app --host 0.0.0.0 --port 8080
```

---

## 📝 维护命令

```bash
# 查看运行日志
tail -f /opt/agent/app.log

# 重启服务（方式1：先激活环境）
pkill -f gunicorn
cd /opt/agent
conda activate faultagent
nohup gunicorn -w 4 -k uvicorn.workers.UvicornWorker fault_diagnosis.app:app --bind 0.0.0.0:8000 > app.log 2>&1 &

# 重启服务（方式2：使用 conda run，无需激活）
pkill -f gunicorn
cd /opt/agent
nohup conda run -n faultagent gunicorn -w 4 -k uvicorn.workers.UvicornWorker fault_diagnosis.app:app --bind 0.0.0.0:8000 > app.log 2>&1 &

# 重建知识库
cd /opt/agent
conda activate faultagent
python -c "from fault_diagnosis.knowledge_base import rebuild_knowledge_base; print(rebuild_knowledge_base())"

# 更新代码后重新安装依赖
conda activate faultagent
pip install -r requirements.txt

# 前端重新构建
cd /opt/agent/agent_fronted
npm run build
```

---

## 🔒 安全建议

1. **修改默认密码**：生产环境请修改所有默认密码
2. **限制访问**：配置防火墙，仅允许内网访问
3. **定期备份**：备份数据库和 faiss_db 目录
4. **日志监控**：定期检查 app.log 异常
5. **固定 SESSION_SECRET**：不要依赖开发态临时密钥，否则服务重启后旧 cookie / 旧 thread 映射会全部失效
6. **核对 FRONTEND_ORIGINS**：避免把 `localhost` / `127.0.0.1` 留在生产配置中

---

## 📞 部署检查清单

部署完成后，请确认以下功能正常：

- [ ] 后端服务启动无报错
- [ ] 前端页面能正常访问
- [ ] 能正常连接 MySQL 数据库
- [ ] 能正常连接 PostgreSQL 数据库
- [ ] 能正常连接 Ollama 服务
- [ ] 能正常查询知识库
- [ ] 能生成图表并保存
- [ ] 能生成报告并保存
- [ ] SSE 流式输出正常

---

**部署完成！** 🎉
