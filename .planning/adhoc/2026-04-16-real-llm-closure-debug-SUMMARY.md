---
mode: debug
date: 2026-04-16
owner: codex
status: completed
objective: 修复真实 LLM 流式 token 丢失与短回复无 token 问题，并完成 SSE、工具、assistant 落库、历史恢复与重启后连续性的闭环验证。
---

# Real LLM Closure Debug 总结

## 已真实打通
- 真实 `/chat/stream` SSE：中英文简单问答与工具调用均能收到 `start`、`token`、`complete`，工具路径能收到 `tool_start` / `tool_end`。
- 真实工具链路：`write_todos`、`get_time`、`query_knowledge_base` 已在 SSE 中可见，assistant 最终回复包含知识库结果。
- PostgreSQL 持久化：checkpoint 中已写入 human / ai / tool / todos，历史接口可读到完整对话。
- 刷新与重启恢复：在固定 `SESSION_SECRET` 条件下，刷新后历史仍可恢复；服务重启后，同一 cookie / thread 可以继续追问并读到先前上下文。

## 本轮修复
- 修复 `fault_diagnosis/streaming.py` 仅发送首个可显示 token 的缺陷，恢复后续 token 连续流出。
- 修复短英文或短回复在流阶段没有 token、只在 `complete` 中出现最终内容的问题。
- 在流阶段没有可显示 token 但最终已有 assistant 内容时，于 `complete` 前补发一次 token，避免前端长期停留在等待状态。

## 保留风险
- 仓库默认本地启动仍未读取到固定 `SESSION_SECRET`；本轮重启恢复验证是通过向进程显式注入固定 secret 完成的。
- 当前 `faiss_db` 仅为 2 chunk smoke index，只证明知识库工具链路可用，不代表全量生产知识库已完成。
- `glm-5` / TokenHub 流式前导仍存在大量 reasoning-only chunk，首个可显示 token 仍可能明显晚于首个 SSE 事件。
