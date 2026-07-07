# Agent Engine V2 Phase 0 Baseline

本文冻结 Agent Engine V2 迁移前的当前主链路行为。Phase 0 只建立可比较基线和保护网，不创建 V2 空引擎，不接入 V2 执行分支，不改变任何生产路径默认行为。

## 当前契约继承清单

V2 后续实现必须继承下列外部契约，除非另有迁移方案、兼容期和前端/调用方配套改造。

| 契约面 | 当前行为 | V2 必须继承 |
| --- | --- | --- |
| `/chat/stream` | `GET` SSE 主聊天入口；`message` 必填，`thread_id`、`user_identity`、`stream_id` 可选；真实身份来自服务端 session/cookie。 | 路径、方法、query、SSE 外壳、session thread 归属校验、cookie 行为和不信任 `user_identity` 的授权边界。 |
| `/chat/stream/edit` | 编辑指定用户轮次后重新生成；会截断目标轮次后的历史，并清理过时 artifact。 | 编辑语义、历史截断、过时 artifact 清理、SSE 契约和权限边界。 |
| `/chat/plan` | goal-native plan 调试快照；仅 `ENABLE_PLAN_ENDPOINT` 或 `LOCAL_DEV_MODE` 启用；无工具、LLM、artifact 副作用。 | 默认关闭、只读、无副作用、返回 `agent_plan_snapshot.v2` 兼容字段。 |
| `/agent/chat` | 语音网关 JSON 入口；内部消费同一条 SSE 主链路并聚合 `token`、`complete`、`tool_end`。 | 不复制独立 runner；继续复用主链路；响应保留 `reply_text`、`visual_actions`、`session_id`、`thread_id`、`metadata`。 |
| `/chat/stop` | 按当前 session 取消活跃 stream；不允许取消其他 session 的 stream。 | `stream_id`/`reason` 请求形状、403 归属校验和 `{ ok, status, ... }` 响应外壳。 |
| SSE | 常见序列 `start -> task_update* -> ping* -> tool_start/tool_end* -> token -> complete`；错误为 `server_error`。 | `type`、`trace_id`、`thread_id`、`stream_id`、`complete` 兼容字段和敏感信息脱敏。 |
| 报告 | `GET /reports/{filename}` 只读 `trash/run/reports/` 下安全 `.html` 文件；普通用户需 `.access.json` 范围校验，管理员可读全部。 | 安全文件名、目录逃逸防护、报告权限、404/403 语义和 `text/html` 输出。 |
| 工单 | `/api/workorders*` 提供本地工单记录；Agent 主链路只输出建议或草稿边界，不自动派发。 | 工单创建/列表/详情/更新 API 外壳、授权设备过滤、禁止派发/执行类状态。 |
| 历史与 Todo | `/ai/history/{type}`、`/ai/history/{type}/{chat_id}`、`DELETE /ai/history/{type}/{chat_id}`、`/api/todos/{thread_id}` 都按当前 session 过滤。 | 历史归属过滤、分页响应兼容、无权限详情返回空列表、删除只限当前 session 拥有记录。 |
| 权限 | `AuthContext` 来自服务端 session/cookie；覆盖 SQL、RAG、report、workorder、tool、高风险动作和 thread ownership。 | 不能信任前端身份字段；V2 candidate plan 不能绕过服务端 RBAC/ABAC/policy 校验。 |

## V2 Feature Flag 设计

Phase 0 新增惰性配置草案：

```python
AGENT_ENGINE_VERSION_DEFAULT = "legacy"
AGENT_ENGINE_VERSION_CHOICES = {"legacy", "v2_plan", "v2_shadow", "v2"}
AGENT_ENGINE_VERSION = _env_choice(
    "AGENT_ENGINE_VERSION",
    AGENT_ENGINE_VERSION_DEFAULT,
    AGENT_ENGINE_VERSION_CHOICES,
)
```

模式含义：

| 值 | 目标语义 | Phase 0 行为 |
| --- | --- | --- |
| `legacy` | 继续使用当前限制型单 Agent 主链路。 | 默认值；唯一实际行为。 |
| `v2_plan` | 后续只生成 V2 plan，不执行工具。 | 仅配置值可解析；不被路由或 runner 使用。 |
| `v2_shadow` | 后续 legacy 执行，V2 旁路生成 plan/diff。 | 仅配置值可解析；不被路由或 runner 使用。 |
| `v2` | 后续切到 V2 validated plan/runtime。 | 仅配置值可解析；Phase 0 禁止默认或隐式启用。 |

Phase 0 禁止事项：

- 不在 `/chat/stream`、`/chat/plan`、`/agent/chat`、`ChatService`、`RestrictedSingleAgentRunner` 或工具节点中读取 `AGENT_ENGINE_VERSION`。
- 不创建 `fault_diagnosis/agent_engine/` 空引擎。
- 不让 `AGENT_ENGINE_VERSION=v2*` 改变任何线上响应、工具调用、artifact、报告、工单或历史写入。
- 非法 `AGENT_ENGINE_VERSION` 必须回退 `legacy`。

## 测试矩阵

| 测试/脚本 | 覆盖行为 | V2 继承要求 |
| --- | --- | --- |
| `tests/test_plan_endpoint.py` | `/chat/plan` 默认关闭、可信身份、只读无副作用。 | 必须继承。 |
| `tests/test_agent_authorization_flow.py`、`tests/test_security_authorization.py` | session/cookie 权限、workflow/tool/data/report 边界。 | 必须继承。 |
| `tests/test_workflow_routing.py`、`tests/test_goal_set.py`、`tests/test_task_family.py`、`tests/test_policy_goalset_migration.py` | goal-native 路由、policy、节点启停和兼容投影。 | 必须继承输出兼容；内部可由 V2 替代。 |
| `tests/test_single_agent_lightweight.py` | 当前主链路轻量端到端行为、工具事件和 complete payload。 | 必须作为 legacy baseline 对比。 |
| `tests/test_conversation_context_phase2.py`、`tests/test_context_manager.py`、`tests/test_workorder_artifact_followup.py`、`tests/test_history_artifact_fallback.py` | 多轮上下文、artifact 复用、历史 fallback。 | 必须继承。 |
| `tests/test_report_payload.py`、`tests/test_report_source_resolution.py` | 报告 payload、证据来源和渲染输入。 | 必须继承。 |
| `tests/test_workorder_feature.py`、`tests/test_manual_confirmation_contract.py` | 工单建议、草稿边界、高风险确认。 | 必须继承。 |
| `tests/test_sql_safety.py`、`tests/test_sql_result_parser.py`、`tests/test_knowledge_lookup.py` | SQL 安全、SQL 结果解析、知识库查询。 | 必须继承。 |
| `tests/test_evidence_bundle.py`、`tests/test_output_templates.py`、`tests/test_observability_trace.py` | 证据、输出模板、trace。 | 必须继承或兼容投影。 |
| `tests/evals/agent_workflow_cases.yaml` + `tests/evals/run_plan_eval.py` | 机器可读 plan baseline cases。 | V2 plan/shadow 对比的主 baseline。 |
| `tests/evals/run_trace_eval.py --subset core` | mock trace、plan-stream consistency、安全短语。 | V2 shadow/切流前硬门禁。 |
| `scripts/auth_acceptance_test.sh`、`scripts/context_acceptance_test.py`、`scripts/goal_acceptance_test.py`、`scripts/stream_context_goal_acceptance_test.py` | 脚本级 API 验收、权限、上下文、stream。 | 发布前验收。 |

Phase 0 实施前基线：`PYTHONPATH=. pytest -q` 通过，结果为 `199 passed, 1 warning`；警告为现有 pytest `asyncio_mode` 配置项未知。

## Baseline Case List

机器可读来源是 `tests/evals/agent_workflow_cases.yaml`。Phase 0 至少冻结以下端到端迁移用例，后续 V2 plan/shadow 必须能逐项对比：

| Case id | 用例 |
| --- | --- |
| `single_status_j1` | 单轮当前状态查询 |
| `single_alarm_code_qa` | 单轮故障码知识问答 |
| `single_alarm_triage_device` | 带设备告警分诊 |
| `single_fault_diagnosis` | 单轮故障诊断 |
| `single_health_assessment` | 单轮健康风险评估 |
| `single_report_generation` | 单轮运行报告生成 |
| `single_action_restart` | 高风险重启动作请求 |
| `single_root_cause_missing_window` | RCA 缺少时间窗口 |
| `composite_alarm_status_resolution` | 复合故障码解释状态处置 |
| `composite_diag_report` | 复合诊断并生成报告 |
| `composite_rca_workorder` | RCA 并判断工单 |
| `composite_severity_recommendation` | 严重性与处置建议 |
| `composite_multi_codes` | 多故障码解释 |
| `composite_metric_trend` | 指标趋势与风险 |
| `composite_report_freshness` | 报告需要披露时效性 |
| `composite_action_workorder_draft` | 动作请求生成工单草稿 |
| `followup_pronoun_severity` | 续问它严重吗复用设备 |
| `followup_resolution_reuse_code` | 续问怎么处理复用故障码 |
| `followup_report_handoff` | 续问基于刚才生成报告 |
| `followup_workorder_from_artifact` | 续问是否需要工单 |
| `followup_refresh_then_workorder` | 刷新当前状态后判断工单 |
| `followup_switch_asset` | 续问切换到 J2 |
| `guest_degraded_diagnosis` | 访客诊断降级 |
| `engineer_denied_asset` | 工程师越权设备拒绝 |

扩展 baseline 还包括 YAML 中的 missing-slot、permission、safety、exception 和 stale artifact cases。新增 V2 case 时必须保留稳定 `id`，并同时给出至少一个正向断言和一个负向断言。

## 迁移检查清单

- `AGENT_ENGINE_VERSION` 默认值保持 `legacy`。
- Phase 0 后 `AGENT_ENGINE_VERSION` 不被任何生产路由、service 或 runner 消费。
- `/chat/stream` 和 `/agent/chat` 仍复用当前限制型单 Agent 主链路。
- `/chat/plan` 仍默认关闭，开启后仍只读且无工具、LLM、artifact 副作用。
- SSE `complete` payload 的推荐字段和兼容字段不删除。
- 报告、历史、Todo、工单和 thread 访问继续执行当前 session/admin/resource 过滤。
- 工单和设备动作仍只能输出建议、草稿或人工确认要求，不能自动派发或执行。
- `PYTHONPATH=. pytest -q` 必须通过。
