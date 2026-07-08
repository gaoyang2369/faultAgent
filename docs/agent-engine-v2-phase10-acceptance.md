# Agent Engine V2 Phase 10 验收报告

## 当前态

- 默认主链路：`AGENT_ENGINE_VERSION=v2`。
- 短期回滚：显式设置 `AGENT_ENGINE_VERSION=legacy` 后，`/chat/stream` 进入旧 `RestrictedSingleAgentRunner`。
- 生产 stream 不再执行 shadow compare、plan diff 或 skill 级切流门禁。
- `workflow_*`、旧任务类型、旧意图字段仅由 V2 output/artifact adapter 单向投影，用于前端和历史 artifact 兼容。

## V2 主链路

```text
/chat/stream
  -> ChatService.stream_chat
  -> agent_runtime.streaming.token_stream_events
  -> AgentEngineV2.plan_only
  -> prepare_v2_execution_plan
  -> WorkflowRuntimeExecutor
  -> agent_engine.output projection
  -> DiagnosisArtifactEnvelope
```

## Legacy 边界

- `single_agent/runner.py` 和 `single_agent/flow.py` 只保留为 legacy rollback。
- 新生产能力不得接入旧 flow。
- 离线 eval compare 可以保留，但不能由 `/chat/stream` 调用。

## 验收记录

- `PYTHONPATH=. pytest -q`：270 passed。
- `PYTHONPATH=. python scripts/goal_native_cutover_check.py`：`internal_forbidden_hits=0`。
- `PYTHONPATH=. python scripts/legacy_dependency_scan.py`：`internal_forbidden_hits=0`。
- `PYTHONPATH=. python tests/evals/run_trace_eval.py --subset smoke`：2 passed，plan-stream consistency 2 passed。

## 后续删除窗口

下一个迭代可以在确认 V2 trace/eval 稳定后删除 legacy rollback：

- 移除 `AGENT_ENGINE_VERSION=legacy` 分支。
- 删除旧 `RestrictedSingleAgentRunner` 默认入口测试。
- 确认 `diagnosis/` 与 `security/` 下的 SQL/report/evidence/workorder helper 稳定后，移除旧 helper wrapper 和 legacy rollback。
