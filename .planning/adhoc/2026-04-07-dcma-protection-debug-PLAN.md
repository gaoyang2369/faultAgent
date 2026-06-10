---
mode: debug
date: 2026-04-07
owner: codex
status: completed
objective: 在保护 DCMA 主流程、接口和构建稳定性的前提下，识别并剥离机械臂业务代码，修复已确认 bug 与风险点，并补足最小必要验证。
---

# DCMA 保护 / 机械臂剥离计划

## 已确认边界

### A 类：DCMA 核心且必须保留
- `app.py`
- `streaming.py`
- `middleware.py`
- `prompts/dynamic_prompt.py`
- `tools/sql_tools.py` 中的 DCMA SQL toolkit
- `tools/report_tools.py`
- `tools/kb_tools.py`
- `tools/utility_tools.py`
- `db_pool.py`
- `logger.py`
- `session_store.py`
- `agent_fronted/src/views/CustomerService.vue`
- `agent_fronted/src/components/*`

### B 类：机械臂专属，应剥离
- `tools/data_tools.py`
- `tools/subagent/`
- `prompts/system_prompt.py` 中的机械臂诊断、SHAP、J3/J6 流程
- `agent_fronted/src/config/questionTemplates.ts` 中机械臂模板
- `agent_fronted/src/App.vue` / `agent_fronted/src/utils/identityUtils.ts` 中机械臂/花卉业务角色逻辑

### C 类：共享公共层，应保留并解耦
- 报告生成、知识库检索、时间工具
- SSE 流、Todo 面板、历史接口
- MySQL/PostgreSQL 连接管理
- 前端聊天框架、消息渲染、静态报告挂载

### D 类：已确认风险/问题
- `streaming.py` 在 `LOCAL_DEV_MODE` 下调用参数不匹配
- `app.py` 流式路由把 `HTTPException` 误吞为 500
- 主系统默认工具注册仍直接暴露机械臂能力
- 默认系统提示词同时混入 DCMA 与机械臂流程，边界不清
- HTML 报告写入缺少基础脚本注入清洗
- 子 Agent 的异步工具内部仍有同步外部请求阻塞风险

## 实施策略

1. 先拆注册边界，不直接删除机械臂代码。
2. 机械臂逻辑迁入独立目录，主系统默认不加载。
3. 保留最小兼容层，避免历史 import 立即失效。
4. 只做可验证、可回退的重构，不改现有 HTTP 路由契约。
5. 回归验证以 DCMA 主流程和现有测试为优先。

## 验证清单

- `python -m pytest`
- 前端 `npm run build`
- 默认工具清单不含机械臂工具
- `LOCAL_DEV_MODE` 下 SSE 可完成闭环
- DCMA 相关 SQL toolkit 仍由主流程注册
