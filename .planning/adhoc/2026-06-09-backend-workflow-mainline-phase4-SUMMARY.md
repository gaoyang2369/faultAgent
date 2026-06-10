---
mode: execute-phase
date: 2026-06-09
task: backend-workflow-mainline-phase4
---

# 后端 Workflow 主链路 Phase4 总结

## 完成内容

- `fault_diagnosis/config.py`：将 `ENABLE_WORKFLOW_V1` 默认值改为 `true`，让 Workflow V1 成为普通聊天默认候选；测试和部署仍可通过环境变量显式关闭。
- `fault_diagnosis/streaming.py`：新增 `_should_use_workflow_mainline()`，集中表达主链路选择规则：
  - 普通 `/chat/stream` 默认走 Workflow V1。
  - `/chat/stream/edit` 的 `replace_history=True` 保留 legacy fallback。
  - dev mode 保留模拟流路径。
  - 独立报告 handoff 行为保持不变。
- `fault_diagnosis/workflows/scenarios/manual_qa.py`：手册问答完成阶段保存 `WorkflowArtifactEnvelope`，补齐后续证据复核与 Phase4 complete 契约增强所需 artifact。
- `tests/test_config.py`：补充 Workflow V1 默认开启与环境变量关闭测试。
- `tests/test_workflow_stream.py`：补充 workflow 主链路调度边界测试，覆盖编辑重生成不误入 workflow。
- `tests/test_manual_qa_flow.py`：补充 `manual_qa` artifact 落库断言。
- `docs/backend-refactor-roadmap.md`：同步 Phase4 当前进度、状态和下一步建议。

## 验证

- `python -m compileall fault_diagnosis tests`：通过。
- `git diff --check`：通过；仅输出当前工作区已有 CRLF 换行提示。

## 未完成验证

- `python -m pytest tests/test_config.py tests/test_workflow_stream.py tests/test_manual_qa_flow.py tests/test_workflow_phase4_contract_adapter.py tests/test_workflow_runner.py -q`：未运行，当前默认 Python 为 3.14 且缺少 pytest。
- `conda run -n faultagent ...`：未运行，当前 shell 找不到 `conda` 命令。

## 后续建议

在 `faultagent` / Python 3.12 环境补跑 Phase1-4 定向测试后，再进入 Phase5：history index、artifact/governance/PDF repository 边界与持久化健康检查。
