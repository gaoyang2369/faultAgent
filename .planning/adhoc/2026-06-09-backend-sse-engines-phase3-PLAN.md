# Backend SSE Engines Phase3 Plan

## 目标

继续 Phase3，将 `streaming.py` 中的执行路径拆到 `agent_runtime/`，让 `streaming.py` 逐步退化为兼容入口和调度层。

## 范围

- 新增 `agent_runtime/workflow_engine.py`：封装 Workflow / report handoff / dev 模拟 SSE 路径的适配。
- 新增 `agent_runtime/legacy_react_engine.py`：封装 LangGraph ReAct legacy 流、工具生命周期、非流式回退、自动补证和 complete 组装。
- 保留 `fault_diagnosis.streaming.token_stream_events`、`stream_workflow_events`、`build_diagnosis_runtime_payload`、`_run_auto_evidence_supplement` 等旧 patch 入口，避免破坏现有测试与兼容调用。
- 不改变 HTTP API、SSE 事件名和 payload 契约。

## 验收

- `streaming.py` 明显瘦身，不再直接承载 legacy ReAct 主执行循环。
- Workflow 与 legacy 执行路径都通过 `sse_adapter.py` 输出 SSE 帧。
- 现有测试的 monkeypatch 路径仍可生效。
- 编译、diff check 和可用的轻量 smoke 通过；pytest 仍受当前 Python 3.12 环境可用性影响。

## 风险控制

- 本轮只搬移代码和注入依赖，不改变业务逻辑。
- 采用依赖注入把旧测试 patch 路径从 `streaming.py` 传入 engine。
- 先保留 streaming 中的兼容 helper 包装，后续 Phase6 再迁移测试 patch 目标。
