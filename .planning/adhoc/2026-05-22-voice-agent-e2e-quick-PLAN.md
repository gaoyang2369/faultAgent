---
mode: quick
date: 2026-05-22
task: voice-agent-e2e
---

# 语音 Agent 端到端链路打通计划

## 目标

在不改变既有公开接口的前提下，补齐语音后端、Agent 后端和前端之间的最小兼容层，使语音链路能够稳定传递 `session_id`，并返回 `reply_text` 与 `visual_actions`。

## 范围

- Agent 后端新增 `POST /agent/chat` 兼容接口，保留现有 `/chat/stream`。
- 语音后端优先调用 `/agent/chat`，并兼容旧 SSE `/chat/stream`。
- 语音后端下行消息补充 `session_id`、`visual_actions` 与 `tts_audio_chunk` 兼容字段。
- 前端语音问答不再重复触发普通文字聊天，改为消费语音后端返回的 Agent/TTS/visual actions。
- 远程语音后端若不是 Git 仓库，修改前仅备份单个将改动文件。

## 风险与约束

- 本地 `.git/refs` 为只读，无法创建本地分支；改动将先在工作区和 `/tmp` 隔离副本验证。
- 当前远程前端实际运行目录为 `/data/fault-diagnosis/agent_fronted`，不在用户限定的 `/media/lenovo/` 下；未经确认不修改该目录。
- 不读取或输出 `.env` 明文内容，不记录密码、私钥、token、数据库密码或 API key。

## 验证

- Python 语法检查：`python -m py_compile` 覆盖新增/修改的后端文件。
- 前端类型/构建检查优先使用现有 npm 脚本；若依赖或目录限制导致不能执行，记录原因。
- 远程部署前后检查服务端口、健康接口、WebSocket 握手和 `/agent/chat` 可用性。
