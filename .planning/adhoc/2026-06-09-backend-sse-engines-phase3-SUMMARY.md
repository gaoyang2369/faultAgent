# Backend SSE Engines Phase3 Summary

## 完成内容

- 新增 `fault_diagnosis/agent_runtime/workflow_engine.py`，封装 Workflow / report handoff / dev 模拟 SSE 的 chunk 适配。
- 新增 `fault_diagnosis/agent_runtime/legacy_react_engine.py`，承接原 `streaming.py` 中的 LangGraph ReAct 主循环、工具生命周期、非流式回退、自动补证和 complete 组装。
- `fault_diagnosis/streaming.py` 已瘦身为兼容调度入口：
  - 保留 `token_stream_events` 外部入口。
  - 保留现有测试依赖的 `_should_auto_supplement_evidence`、`_should_use_workflow_report_generation`、`_enrich_workflow_sse_chunk` 等 helper。
  - 通过依赖注入保留 `stream_workflow_events`、`build_diagnosis_runtime_payload`、`_run_auto_evidence_supplement` 等旧 patch 点。
- Workflow 和 legacy 执行路径都通过 `sse_adapter.py` 输出兼容 SSE 帧。

## 验证

- `python -m compileall fault_diagnosis tests`：通过。
- `git diff --check`：通过，仅保留既有 CRLF 工作区提示。
- `powershell -ExecutionPolicy Bypass -File .\scripts\run_tests.ps1`：未运行成功，当前机器缺少脚本要求的 Python 3.12 环境。

## 结果

Phase3 的主要拆分目标已完成：`streaming.py` 不再承载 legacy ReAct 主执行循环，SSE 事件模型、SSE adapter、Workflow engine 与 legacy engine 已分层。

后续进入 Phase4 前，应在 `faultagent` / Python 3.12 环境中补跑 SSE、Workflow、chat edit、agent chat 和 service 定向测试。
