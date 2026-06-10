---
mode: execute-phase
date: 2026-06-09
task: backend-workflow-mainline-phase4
---

# 后端 Workflow 主链路 Phase4 计划

## 目标

按照 `docs/backend-refactor-roadmap.md` Phase 4，把 `fault_diagnosis/workflows/` 从可选旁路提升为默认聊天主链路候选；legacy ReAct 仅保留为 workflow 未覆盖、编辑重生成、开发模式或紧急回退路径。

## 范围

- 调整聊天 SSE 调度策略，使普通 `/chat/stream` 默认优先走 Workflow V1。
- 保留 `/chat/stream/edit` 的历史替换场景走 legacy 路径，避免破坏编辑重生成上下文裁剪。
- 保留本地 dev mode 和 workflow 报告 handoff 的既有行为。
- 保持 HTTP 路径、SSE 事件名、complete payload 字段、session/cookie 行为不变。
- 补充测试覆盖默认 workflow 候选和 legacy fallback 边界。
- 更新后端重构路线图和本轮 GSD summary。

## 不做

- 不重写六类场景 runner 的业务实现。
- 不改变 LangChain/LangGraph/FastAPI 版本。
- 不迁移历史持久化或 artifact store 后端，留到 Phase 5。
- 不读取 `.env` 内容，不引入新重依赖。

## 验证

- 运行 `python -m compileall fault_diagnosis tests`。
- 运行 `git diff --check`。
- 优先运行 `tests/test_workflow_stream.py`、`tests/test_workflow_runner.py`、`tests/test_workflow_phase4_contract_adapter.py`、`tests/test_agent_chat_api.py`、`tests/test_chat_edit_api.py`。
- 若当前环境缺少 Python 3.12/pytest 依赖，记录阻塞与已完成替代验证。
