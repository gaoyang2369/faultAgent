---
mode: debug
date: 2026-04-16
owner: codex
status: completed
objective: 真实环境联调 MySQL、PostgreSQL、LLM、Tavily、Ollama/FAISS、外部故障 API 和前后端真实模式。
---

# Real Environment Debug 总结

## 已真实接通
- MySQL：连接、临时写读、业务表只读均成功。
- PostgreSQL：连接、临时写读、LangGraph checkpoint schema 初始化和历史读取均成功。
- Tavily：真实搜索调用成功。
- Ollama：健康检查和单次 embedding 成功。
- 外部故障诊断 API：最小 sequence payload 调用成功。
- 前后端：真实模式后端和前端均启动，首页、代理、历史、todos 接口可访问。

## 未完整打通
- LLM：网关可达但 API key 返回 `401 invalid api key`，真实聊天无法产生 token / tool / complete。
- FAISS：主索引缺失；全量重建超时，小样本索引能写盘但检索阶段调用 embedding 超时。

## 保留修改
- 环境变量优先级统一为进程环境优先，适配真实部署。
- 增加真实模式和流式性能脱敏日志。
- 增加模型鉴权错误分类，避免 401 后重复 fallback。
