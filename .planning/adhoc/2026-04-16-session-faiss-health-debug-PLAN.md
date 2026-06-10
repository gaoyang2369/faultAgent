---
mode: debug
date: 2026-04-16
owner: codex
status: in_progress
objective: 修复 SESSION_SECRET 稳定性、FAISS 建库/检索阻塞，并补齐不触发 LLM 推理的真实依赖健康检查。
---

# Session / FAISS / Health Debug 计划

## 当前前提
- 真实 MySQL、PostgreSQL、Tavily、Ollama、外部故障 API 已完成基础联通。
- LLM 网关当前因 API Key 过期返回 401，本轮不继续深挖模型推理链路。
- `.env` 可能包含密钥，本轮只做脱敏存在性和连通性诊断。

## 本轮目标
- 明确并修复 `SESSION_SECRET` 缺失时的会话稳定性风险。
- 提供固定 secret 下重启前后 cookie / thread 签名一致性的验证路径。
- 将 FAISS 建库从应用启动主路径中剥离为可配置脚本，并补齐批次、超时、最大文档数和增量能力。
- 修复检索超时后仍被线程池退出阻塞的问题。
- 新增不触发真实 LLM 推理的健康检查，快速区分基础设施问题和模型链路问题。

## 风险控制
- 不输出完整 secret、密码、API key 或认证头。
- 不改变既有 HTTP 聊天 / 历史 / todo API 契约。
- 不引入新的重量级依赖。
- 建库和检索验证优先使用小样本，避免再次卡在全量 PDF。
