# Agent Engine V2 Phase 10 验收报告

> 历史验收快照。当前 canonical cutover 状态与命令以 [当前架构总览](./current-architecture.md) 为准；下列通过数只表示当时提交，不是当前基线。

## 当前态

- 唯一主链路：Agent Engine V2。
- `AGENT_ENGINE_VERSION` 只兼容解析为 `v2`；旧全局回滚入口已经删除。
- 生产 stream 不再执行 shadow compare、plan diff 或 skill 级切流门禁。
- `workflow_*`、旧任务类型、旧意图字段仅由 V2 output/artifact adapter 单向投影，用于前端和历史 artifact 兼容。

## V2 主链路

```text
/chat/stream
  -> ChatService.stream_chat
  -> server/agent_gateway/streaming.token_stream_events
  -> AgentEngineV2.build_plan_snapshot
  -> runtime/plan_preparer
  -> WorkflowRuntimeExecutor
  -> agent/output projection
  -> DiagnosisArtifactEnvelope
```

## Legacy 删除结果

- `fault_diagnosis/single_agent/` 已删除。
- `/chat/stream`、`/chat/stream/edit`、`/agent/chat`、`/chat/plan` 均只走 V2。
- V2 失败返回 `server_error`，不自动 fallback。

## 验收记录

- `PYTHONPATH=. pytest -q`：270 passed。
- `PYTHONPATH=. python scripts/goal_native_cutover_check.py`：`internal_forbidden_hits=0`。
- `PYTHONPATH=. python scripts/legacy_dependency_scan.py`：`internal_forbidden_hits=0`。
- `PYTHONPATH=. python tests/evals/run_trace_eval.py --subset smoke`：2 passed，plan-stream consistency 2 passed。

## 删除后验收

- `PYTHONPATH=. pytest -q`。
- `PYTHONPATH=. python scripts/no_single_agent_runtime_dependency_check.py`。
- `PYTHONPATH=. python scripts/legacy_dependency_scan.py`。
