---
mode: debug
date: 2026-04-16
owner: codex
status: completed
objective: 修复真实 LLM 流式 token 丢失与短回复无 token 问题，并完成 SSE、工具、assistant 落库、历史恢复与重启后连续性的闭环验证。
---

# Real LLM Closure Debug 计划

## 当前前提
- MySQL、PostgreSQL、Tavily、Ollama 与 FAISS smoke index 已可用，基础健康检查不再作为本轮主战场。
- OpenAI-compatible LLM 已恢复可用，但 `glm-5` / TokenHub 流式前段存在大量 reasoning-only chunk。
- 本地默认启动未读取到固定 `SESSION_SECRET`；本轮验证会先用固定 secret 注入进程，避免把会话恢复问题和 SSE / LLM 问题混在一起。

## 本轮目标
- 复现并修复 `/chat/stream` 的 token 发射缺陷，确保不是只有 `complete` 没有 `token`。
- 验证简单问答与工具/知识库路径下的真实 SSE 事件顺序、首 token 时间和完成事件。
- 验证 assistant 内容、tools / todos 和 checkpoint 已写入 PostgreSQL。
- 验证 `/ai/history/chat`、`/ai/history/chat/{thread}`、刷新恢复与重启后会话连续性。

## 风险控制
- 不输出完整 API key、数据库密码、SESSION_SECRET 或认证头。
- 不把 `faiss_db` 的 2 chunk smoke index 误报为全量生产知识库。
- 修复只限最小必要范围，保持 HTTP API 契约不变。

## 实际执行结果
1. 真实健康检查确认 MySQL、PostgreSQL、Tavily、Ollama 和 FAISS smoke index 可用；默认本地启动仍未读取到固定 `SESSION_SECRET`。
2. 直连 `glm-5` / TokenHub 复现 reasoning-only 流式前导 chunk；中文与英文最小流式均可返回显示内容。
3. 定位 `/chat/stream` 的核心缺陷：后端只发出首个可显示 token，后续 token 全部丢失；短英文回复会直接 `complete` 而没有任何 `token`。
4. 已最小修复 `fault_diagnosis/streaming.py`：放宽短英文首段可见性判断、恢复后续 token 连续发射、并在仅有最终内容时于 `complete` 前补发 token。
5. 修复后真实回归通过：中英文简单问答与知识库工具路径均收到 `start -> token -> complete` 或带 `tool_start/tool_end` 的完整 SSE 序列。
6. `query_knowledge_base` 与 `write_todos` 已在真实链路触发，assistant 最终回复消费了工具结果；`faiss_db` 仍仅为 2 chunk smoke index。
7. PostgreSQL checkpoint 已包含 human / ai / tool / todos；`/ai/history/chat`、`/ai/history/chat/{thread}`、刷新恢复与固定 secret 下重启后 follow-up 均成功。
