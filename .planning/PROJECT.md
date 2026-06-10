# AI Agent 后端模块化重组

## What This Is

工业设备故障诊断专家系统的后端代码，已完成模块化重组。原始 592 行 app.py + 597 行 tools.py 已拆分为 8 个职责清晰的模块。新用户 fork 后替换 tools/、prompts/、config.py、middleware.py 即可搭建自己的 Agent 服务。

## Core Value

**模块清晰可替换**：tools/、prompts/、middleware.py、config.py 是项目特定的可替换部分；app.py（路由、lifespan）+ streaming.py（SSE）+ utils.py 是核心不变部分。

## Current State

**v1.0 已发布** (2026-03-27) — 后端模块化重组完成

```
app.py (256行)        ← Lifespan + Routes + CORS + Static files
streaming.py (146行)  ← SSE token_stream_events
config.py (31行)      ← 11 centralized constants
utils.py (204行)      ← JSON sanitize, todo parsing
middleware.py (19行)   ← build_middleware()
knowledge_base.py     ← FAISS KB (config-driven)
tools/                ← 5 domain modules + subagent/
prompts/              ← system_prompt + dynamic_prompt
tests/ (76 tests)     ← Characterization + unit tests
```

**技术栈**：Python 3.12 + FastAPI 0.121.0 + LangChain 1.0.3 + LangGraph 1.0.5 + Vue 3.5.22

## Requirements

### Validated

- ✓ LangGraph ReAct Agent 创建与运行 — v1.0
- ✓ 中间件管线（TodoList、动态Prompt、上下文摘要） — v1.0
- ✓ SSE 流式 Token 级响应 — v1.0
- ✓ FastAPI REST/SSE 端点 — v1.0
- ✓ PostgreSQL 会话状态持久化 — v1.0
- ✓ MySQL 业务数据查询 — v1.0
- ✓ FAISS 知识库检索 — v1.0
- ✓ 子Agent 以 Tool 形式接入 — v1.0
- ✓ 数据可视化图表生成 — v1.0
- ✓ HTML/Markdown 报告生成 — v1.0
- ✓ 对话历史与 Todo 管理 API — v1.0
- ✓ 特征测试安全网（22 tests） — v1.0 Phase 1
- ✓ 安全前置条件（密钥清理） — v1.0 Phase 1
- ✓ 配置集中（8 个硬编码值外化） — v1.0 Phase 2
- ✓ 通用工具提取（utils.py） — v1.0 Phase 2
- ✓ 知识库配置化 — v1.0 Phase 2
- ✓ Tools 模块化（tools/ 5 模块） — v1.0 Phase 3
- ✓ 延迟初始化（懒加载单例） — v1.0 Phase 3
- ✓ 子 Agent 迁移（tools/subagent/） — v1.0 Phase 3
- ✓ Prompts 分离（prompts/ 目录） — v1.0 Phase 4
- ✓ 中间件组装（middleware.py） — v1.0 Phase 4
- ✓ 知识库完全配置化 — v1.0 Phase 4
- ✓ app.py 瘦身（256 行） — v1.0 Phase 5

### Active

None — planning next milestone.

### Out of Scope

- 前端 Vue 项目重构 — 本次只重构后端 Python 部分
- 数据库架构变更 — MySQL + PostgreSQL 架构不变
- Python 包发布 — 不将项目发布为 pip 包
- Protocol 接口 / ABC 抽象 — 保持简单
- agent_core/ + projects/ 分层 — 在单项目内模块化

## Context

**关键约束**：
- extract_data + fig_inter 通过 globals() 共享命名空间，在 tools/data_tools.py 同文件
- 模块级 DB 连接已改为懒加载单例（_db = None + _get_db()）
- 所有现有 API 端点行为不变，前端无需修改

**目标用户**：自己和其他开发者，fork 后替换 tools/prompts/config 即可搭建不同领域的 Agent

## Constraints

- **Tech Stack**: LangChain 1.0.3 + LangGraph 1.0.5 + FastAPI 0.121.0 — 不升级
- **Compatibility**: 现有故障诊断项目正常运行
- **Frontend API**: 不改变 API 接口契约
- **Dependencies**: 不引入重量级新依赖

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| 模块拆分而非框架抽象 | 目标是代码整理可替换，不需要 Protocol/ABC | ✓ v1.0 |
| globals() 三工具同文件 | extract_data/fig_inter 共享运行时命名空间 | ✓ v1.0 |
| 延迟初始化 DB 连接 | 改善测试和启动体验 | ✓ v1.0 |
| 子 Agent 移入 tools/ | 子 Agent 本质是工具，统一管理 | ✓ v1.0 |
| SSE 提取到 streaming.py | app.py 瘦身到 256 行，路由和流式分离 | ✓ v1.0 |
| 现有测试不改仅复用 | Phase 1 安全网贯穿 5 个 Phase 全绿 | ✓ v1.0 |

## Evolution

This document evolves at phase transitions and milestone boundaries.

---
*Last updated: 2026-03-27 after v1.0 milestone complete — all 5 phases shipped, 22 requirements validated, 76 tests pass*
