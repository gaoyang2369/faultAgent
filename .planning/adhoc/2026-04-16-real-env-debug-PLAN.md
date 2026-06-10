---
mode: debug
date: 2026-04-16
owner: codex
status: completed
objective: 切换出 LOCAL_DEV_MODE，验证 MySQL、PostgreSQL、LLM、Tavily、Ollama/FAISS 和外部故障 API 的真实联调链路。
---

# Real Environment Debug 计划

## 当前前提
- 本地开发模式已经跑通，但本轮目标必须验证真实依赖，不以 mock / fake / dev fallback 作为最终结果。
- 仓库 `.env` 可能包含密钥，本轮只做脱敏存在性和连通性诊断，不输出完整密钥、密码或认证头。
- 上一轮本地开发服务可能仍占用 8000 / 9005，真实后端启动前需要释放端口。

## 本轮目标
- 梳理真实模式配置入口、初始化路径和必需环境变量。
- 禁用 `LOCAL_DEV_MODE=true` 并验证真实启动路径。
- 对 MySQL / PostgreSQL 分别执行连接、schema、读写探针。
- 对 OpenAI-compatible LLM 执行非流式、流式、中文和英文探针。
- 对 Tavily、Ollama/FAISS、外部故障诊断 API 执行最小真实调用。
- 启动真实后端与前端，验证聊天、工具调用、落库、历史恢复和刷新后持久化。

## 风险控制
- 不读取或输出 `.env` 的完整敏感值。
- 所有日志和报告必须脱敏。
- 不升级项目约束依赖，不引入重量级依赖。
- 保持 HTTP API 契约不变，修复仅限真实联调必要问题。

## 实际执行结果
1. `.env` 脱敏审计确认 `LOCAL_DEV_MODE=false`，MySQL / PostgreSQL / LLM / Tavily 关键变量存在；Ollama、FAISS、外部故障 API、CORS、SESSION_SECRET 当前走代码默认值。
2. MySQL 真实连接成功，临时表读写成功，业务表 `real_data` 可读（566 行，31 列样本）。
3. PostgreSQL 真实连接成功，临时表读写成功，LangGraph checkpoint 表已初始化并有数据。
4. 真实后端启动成功，完成 MySQL 连接池、PostgreSQL checkpointer、Agent 初始化；真实前端启动成功。
5. Tavily 最小搜索成功；Ollama `/api/version` 和单次 `embed_query` 成功；外部故障诊断 API 最小 payload 调用成功。
6. OpenAI-compatible LLM 网关可达，但返回 `401 invalid api key`，真实聊天无法产出 token / tool / complete。
7. FAISS 全量重建超过 15 分钟未完成；小样本 FAISS 索引可写盘，但检索阶段再次调用 Ollama embedding 超过 3 分钟未返回。
8. 已修复真实部署环境变量优先级，避免 `.env` 覆盖进程环境变量；已补充真实模式启动、MySQL 连接和首 token / 完成计数日志。
9. 已修复模型网关鉴权错误分类，避免 401 后重复非流式重试，并向前端返回明确配置错误。

## 当前阻塞
- `OPENAI_API_KEY` 当前真实返回 401，缺少有效模型网关凭证，无法完成真实“模型推理 -> 工具调用 -> assistant 落库 -> 历史恢复”闭环。
- `faiss_db` 主索引缺失，全量构建耗时超出本轮工具窗口；Ollama 单次 embedding 可用，但 FAISS 构建/检索链路表现为长时间阻塞。
- `SESSION_SECRET` 未配置，开发环境可启动但重启后旧 cookie / thread 映射会失效。
