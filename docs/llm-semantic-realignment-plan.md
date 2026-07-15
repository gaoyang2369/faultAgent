# LLM 语义层与回答层改造计划

> 状态：Wave 1-4 已完成，Wave 5 待执行
> 目标分支：`refactor/v2`  
> 目标：让 LLM 实际参与意图拆解、上下文理解和回答表达，同时保留 Canonical、权限、Artifact、计划、执行和证据链的确定性边界。

## 1. 结论先行

本轮不推翻 Agent Engine V2，也不把系统改成自由规划 Agent。目标架构确定为：

```text
当前消息
+ PendingClarification
+ ACL 过滤后的上下文候选摘要
        │
        ▼
LLM Semantic Interpreter（每轮调用）
- Clause / Goal 提议
- 实体与文本 span 提议
- 否定、条件、顺序、依赖
- 指代、修正、上下文约束
        │
        ▼
Deterministic Canonicalizer
- Schema / span / capability 校验
- 设备注册表、故障码、时间标准化
- 逐字段 ACCEPT / REJECT / CLARIFY
- 形成 CanonicalTurnRequest
        │
        ▼
现有确定性控制平面
- Authorization / Readiness
- Source Resolution / Exact Artifact Binding
- PlanCompiler / PlanValidator
- Typed Runtime Nodes / EvidenceLedger
        │
        ▼
Canonical Deliverables
        │
        ▼
Grounded Answer LLM（开发环境 100% 尝试）
        │
        ▼
Answer Validator
├── 通过：返回润色答案
└── 失败：返回 CompositePresenter 模板答案
```

最终原则：

- LLM 是语义提议者，不是权限、来源、执行和事实权威。
- Canonical 层是唯一语义收口点，LLM 和规则都不能绕过它。
- 规则从“主要语义权威”调整为快速解析、冲突证据、安全规则和模型失败兜底。
- 开发/测试环境不做慢灰度，完成基础异步改造后直接全量启用 LLM 路径。
- 生产仍保留独立开关和确定性 fallback，切换模型不需要修改业务架构。

## 2. 当前项目的真实差距

### 2.1 意图层

当前已有 `LLMStructuredClauseModel`、`ControlledIntentFallback` 和 Shadow，但还不是 LLM 主语义层：

- 只有规则先判定 eligible，Fallback 才会调用模型；规则词表外的表达可能连调用资格都拿不到。
- 模型只能引用确定性实体，不能提出待验证的新设备别名、故障码或时间表达。
- 规则已有 capability 时，模型无法提出可仲裁的纠错意见。
- 意图模型使用同步 `invoke()`，生产异步链路开启后会阻塞事件循环。
- `/chat/stream` 没有完整的每轮 Shadow 对比；`/chat/plan` 的 Shadow 还是独立补调用。
- 开启 Shadow 与 Fallback 不等于“每轮由 LLM 辅助理解”。

### 2.2 上下文层

当前 `CanonicalContextBinder` 的 ACL、Artifact 完整性、兼容性和 exact binding 边界正确，必须保留；不足在于输入只有规则语义：

- 上下文复用主要依赖“刚才、基于已有、用刚才”等固定词。
- 候选排序固定为上一轮、PendingAction、CaseState、普通 Artifact。
- 无法稳定理解“比较里更差的设备”“最早那次诊断”“电机二保留、电机一排除”等语义约束。
- 不能让 LLM 直接选择 Artifact ID，否则会破坏 ACL、类型、freshness 和 lineage 边界。

### 2.3 回答层

`GroundedAnswerSynthesizer` 已具备正确的结构：异步调用、Answer Source Packet、固定 Schema、确定性 Validator、失败回退模板。这里不需要重构，只需要：

- 开发环境 100% 开启；
- 把调用状态、校验结果和 fallback 原因纳入统一观测；
- 保持只消费授权后的 deliverables、claims、evidence 和限制信息；
- 不允许回答模型重新诊断或改变报告、工单、权限状态。

模型供应商的超时和模型选型不属于本轮架构阻塞项，后续替换官方模型后单独调参。

## 3. 新增核心合同

### 3.1 `SemanticTurnProposal`

LLM 每轮输出一份非权威提议，建议字段如下：

```python
SemanticTurnProposal(
    clauses=[
        SemanticClauseProposal(
            text_span=(start, end),
            capability="diagnose_fault",
            confidence=0.92,
            entities=[...],
            requested=True,
            negated=False,
            conditional=False,
            sequence_index=0,
            depends_on_clause_indexes=[],
            context_reference=ContextReferenceProposal(...),
        )
    ],
    ambiguities=[],
)
```

实体提议必须带原文 span。LLM 可以提出 normalized candidate，但不能直接创造可信实体：

- 设备：必须通过资产注册表别名解析；无法唯一解析则 `CLARIFY`。
- 故障码：必须通过原文 span、格式和领域校验。
- 时间：必须由确定性时间解析器标准化。
- Artifact：LLM 不得输出真实 Artifact ID。

### 3.2 `SemanticFieldDecision`

Canonicalizer 对模型提议逐字段输出裁决，而不是整份覆盖：

```python
SemanticFieldDecision(
    field="clauses[0].capability",
    decision="ACCEPT",  # ACCEPT | REJECT | CLARIFY
    value="diagnose_fault",
    reason_code="valid_allowlisted_capability_with_grounded_span",
)
```

裁决规则：

1. 越权、非白名单能力、无有效 span、未知实体：`REJECT`。
2. 两个合理解释会改变执行节点或目标设备：`CLARIFY`。
3. 低风险语义、Schema 合法、span/实体可验证：`ACCEPT`。
4. 否定或高风险动作冲突时选择不执行并追问，不能乐观执行。
5. 模型不可用或整份输出非法时，使用确定性解析结果；确定性也无法确认则追问。

### 3.3 `ContextSemanticProposal`

LLM 只输出上下文约束，不输出最终绑定：

```python
ContextSemanticProposal(
    reference_target="prior_diagnosis_result",
    temporal_relation="earliest",  # previous | latest | earliest | ordinal
    include_asset_refs=["G120电机2"],
    exclude_asset_refs=["G120电机1"],
    requested_reuse=True,
    freshness_intent="historical_ok",
    relation="worse_device_from_previous_comparison",
    confidence=0.91,
)
```

`ContextProposalValidator` 先验证约束，`CanonicalContextBinder` 再在 ACL 过滤后的候选集中做唯一选择。Artifact exact ID、权限、完整性、类型、freshness 和 lineage 仍全部由确定性代码决定。

## 4. 代码结构

不要继续扩大 `semantic_fallback.py` 或 `context_binding.py`。新增独立小模块：

```text
fault_diagnosis/agent/semantics/
├── contracts.py              # LLM 提议、字段裁决、观测合同
├── model_gateway.py          # 单次异步模型调用、超时、取消、Schema 输出
├── intent_interpreter.py     # 构造意图语义输入与 prompt
├── intent_canonicalizer.py   # span/entity/capability 校验与冲突仲裁
├── context_interpreter.py    # 构造 ACL-safe 上下文语义输入
├── context_validator.py      # 上下文约束校验
└── service.py                # 每轮只调用一次并编排意图/上下文提议
```

现有模块职责调整：

- `agent/canonical_turn/parser.py`：保留确定性解析，只负责产出规则候选和最终 parse 投影，不直接拥有模型调用。
- `agent/canonical_turn/semantic_fallback.py`：迁移后降为兼容适配器，最终删除。
- `agent/canonical_turn/llm_structured_clause_model.py`：迁移到异步 `model_gateway.py`，不再使用同步 `invoke()`。
- `agent/canonical_turn/coordinator.py`：生产 preview/execute 改为 async semantic resolution，再构建 Canonical 请求。
- `agent/canonical_turn/context_binding.py`：接收经过验证的 `ContextSemanticProposal`，但继续拥有最终绑定权。
- `agent/canonical_turn/intent_shadow_service.py`：不再二次调用模型，只投影同一次语义调用的 diff/metrics。
- `server/use_cases/turn_execution.py`：`preview_turn()` 改为可 await；stream 与 plan 共用同一个语义结果。
- `server/use_cases/chat_service.py`：删除 plan-only Shadow 补调用，避免同一轮重复请求模型。
- `server/agent_gateway/answer_synthesis.py`：保持现有边界，只统一配置与观测字段。
- `platform/settings.py`：引入单一语义模式配置，旧布尔开关保留一版兼容映射。

## 5. 配置策略

用模式配置替代两个容易互相冲突的 Intent 布尔开关：

```env
# off | shadow | primary
LLM_SEMANTIC_MODE=primary
ENABLE_LLM_CONTEXT_SEMANTICS=true

# 模型失败时使用确定性解析；两边都不确定则追问
LLM_SEMANTIC_FAILURE_POLICY=deterministic_fallback

# 回答层在开发/测试环境全量尝试
ENABLE_GROUNDED_ANSWER_SYNTHESIS=true
GROUNDED_ANSWER_ROLLOUT_PERCENT=100
ANSWER_MODEL_INCLUDE_DETERMINISTIC_FALLBACK=true
```

兼容期映射：

- `ENABLE_LLM_INTENT_SHADOW=true` → `LLM_SEMANTIC_MODE=shadow`；
- `ENABLE_LLM_INTENT_FALLBACK=true` → 仅作为旧 fallback 行为，不代表 primary；
- 新配置显式存在时，新配置优先；
- 一版后删除旧开关和旧 fallback 专用 prompt。

开发环境完成异步入口后直接使用 `primary + context=true + answer=100`，不执行 5%→25% 的慢灰度。生产配置仍默认关闭，待最终验收后一次切换。

## 6. 加速实施顺序

### Wave 1：打通统一异步语义入口

目标：先让“全部开启”在架构上成立，而不是只改 `.env`。

任务：

1. 新增 `agent/semantics` 合同与异步 `model_gateway`。
2. `ConversationTurnCoordinator` 增加异步 preview 主入口。
3. `/chat/plan`、`/chat/stream` 共用同一次 semantic result。
4. 接通取消、超时、并发信号；模型失败只降级语义层，不影响确定性控制平面。
5. 新增统一 trace：attempted、mode、schema、accepted/rejected/clarify、fallback、latency、token。

完成标志：开发环境可把三个 LLM 能力全部打开，普通 stream 每轮最多一次语义模型调用、最多一次回答模型调用。

### Wave 2：LLM 主意图拆解 + Canonical 仲裁

任务：

1. LLM 每轮解析 clause、goal、实体候选、否定、条件、顺序、依赖和修正。
2. 模型允许提出规则未识别的实体候选，但必须通过 span 和 registry/格式验证。
3. 把 `IntentSemanticMerger` 的“只能填空”升级为逐字段 `ACCEPT / REJECT / CLARIFY`。
4. 允许模型指出规则 capability 可能错误；冲突不再固定“规则永远赢”。
5. `CanonicalTurnRequest` 仍是 planner 唯一输入，不新增第二套生产意图合同。
6. `dispatch_workorder`、设备控制、权限语义只能识别，不能直接形成可执行授权。

完成标志：非标准表达、复合请求、否定/条件/修正可由 LLM 改善，同时 planner、权限和工具入口只读取 Canonical 结果。

### Wave 3：LLM 上下文语义 + 确定性绑定

任务：

1. 在 `project_authorized_context_candidates()` 之后调用 Context Interpreter。
2. 输入只含当前消息、Pending 摘要和 ACL-safe candidate metadata；不传未授权 Artifact、原始工具结果或完整历史正文。
3. 支持 previous/latest/earliest/ordinal、包含/排除设备、比较结果角色、历史复用和 freshness 意图。
4. Binder 根据验证后的约束过滤和排序；唯一候选自动绑定，近似候选追问。
5. exact artifact loading 和二次 ACL/lineage 校验保持不变。

完成标志：能处理“刚才更差的设备”“最早那次诊断”“只要电机二”“前面故障码和刚查异常是否相关”等表达，且不出现越权继承和模糊自动选择。

### Wave 4：回答层全量接通与统一兜底

任务：

1. 开发环境 Grounded Answer 100% 尝试。
2. 保持一次模型调用、固定 JSON Schema 和 `GroundedAnswerValidator`。
3. 明确四类兜底：语义模型失败→规则；语义冲突→追问；上下文不唯一→追问；回答模型失败→模板。
4. 最终 payload/trace 明确标记 final answer source，前端无需猜测是否使用 LLM。

完成标志：所有正常、部分成功、blocked、denied、failed 结果都能稳定返回，模型永远不能改变执行状态或新增无证据事实。

### Wave 5：集中测试与删除旧权威

按用户要求，主体实现完成后集中跑全量回归；开发中只保留必要的合同级测试，避免结构改完后一次性定位大量低级错误。

任务：

1. 删除生产链对旧 fallback eligibility 正则的依赖。
2. Shadow 只做同一次结果的对比观测，不再成为第二调用链。
3. 统一 capability 定义，消除 `_REQUIRED_SLOTS`、规则、Skill YAML 和 PlanCompiler 多处漂移。
4. 更新当前架构文档、环境变量说明和 trace contract。

## 7. CapabilitySpec 收口

新增统一 registry，至少覆盖：

```python
CapabilitySpec(
    capability="diagnose_fault",
    required_slots=("device",),
    optional_slots=("time_window", "fault_code"),
    allowed_source_types=("sql_artifact", "analysis_artifact"),
    runtime_nodes=("sql", "rag", "analysis"),
    risk_level="medium",
    user_visible=True,
    llm_may_propose=True,
    approval_required=False,
)
```

以下组件从同一 registry 读取：

- LLM output schema 的 capability 白名单；
- required/optional slots；
- Canonicalizer；
- Context artifact compatibility；
- Planner/Validator；
- 测试参数化用例。

这项与 Wave 2 同步完成，不另开长期重构阶段。

## 8. 验收标准

### 8.1 架构硬门槛

- Planner、Authorization、Source Resolution、Artifact Binding、Runtime 不读取原始 LLM proposal。
- 每轮语义模型调用次数 `<= 1`，回答模型调用次数 `<= 1`。
- LLM 不接收未经过 ACL 投影的 Artifact 内容或候选。
- LLM 不输出或选择最终 Artifact ID。
- 未知 capability、无效 span、未知设备和越权候选进入 REJECT/CLARIFY。
- 模型故障不会绕过 Canonicalizer，也不会导致自动派单或设备操作。
- deterministic fallback 和回答模板 fallback 始终可用。

### 8.2 意图指标

- 现有 72 条语义集不低于当前确定性基线：capability F1 `0.9392`，false positive activation `0`。
- 新增真实/口语/复合/省略/修正表达，发布前总量不少于 200 条。
- capability F1 `>= 0.95`；实体引用 F1 `>= 0.96`。
- 否定、条件、依赖准确率分别 `>= 0.98`。
- 高风险误激活率、越权 capability 接受数均为 `0`。
- 所有 rule/model 冲突都有字段级 decision 和 reason code。

### 8.3 上下文指标

- explicit binding accuracy `>= 0.99`。
- 语义指代和历史选择准确率 `>= 0.95`。
- unauthorized inheritance、failed/denied artifact reuse、ambiguous auto-selection 均为 `0`。
- 当前状态请求错误复用 stale artifact 为 `0`。
- 历史结果足够时的不必要工具调用率为 `0`。

### 8.4 回答指标

- 无证据新增事实、状态篡改、权限篡改、虚假派单均为 `0`。
- Answer Validator 通过率 `>= 0.98`；未通过时模板回退率 `100%`。
- latest available / stale / limitation 披露准确率 `100%`。
- 复合请求逐项覆盖成功、失败和 blocked 结果。

### 8.5 回归命令

```bash
PYTHONPATH=. pytest -q
PYTHONPATH=. python tests/evals/run_intent_shadow_eval.py --mode compare
PYTHONPATH=. python tests/evals/run_context_binding_eval.py
PYTHONPATH=. python tests/evals/run_context_goal_regressions.py
PYTHONPATH=. python tests/evals/run_plan_eval.py --tier core
PYTHONPATH=. python tests/evals/run_canonical_output_regressions.py
PYTHONPATH=. python tests/evals/run_agent_v2_e2e_eval.py
PYTHONPATH=. python scripts/legacy_dependency_scan.py --strict --json
PYTHONPATH=. python scripts/goal_native_cutover_check.py --strict
```

官方模型接入后再执行真实模型指标，不把当前供应商超时作为架构验收结论。

## 9. 首批文件级任务

| 优先级 | 文件/目录 | 修改内容 |
|---|---|---|
| P0 | `fault_diagnosis/agent/semantics/*` | 新增统一提议合同、异步模型入口、意图/上下文解释和裁决 |
| P0 | `fault_diagnosis/agent/canonical_turn/coordinator.py` | async preview/execute 语义入口，Canonical 仍为唯一生产合同 |
| P0 | `fault_diagnosis/server/use_cases/turn_execution.py` | plan/stream await 同一次 semantic resolution |
| P0 | `fault_diagnosis/server/use_cases/chat_service.py` | 删除 plan-only Shadow 二次模型调用 |
| P0 | `fault_diagnosis/platform/settings.py` | 新增 `LLM_SEMANTIC_MODE` 与兼容映射 |
| P1 | `fault_diagnosis/agent/canonical_turn/context_binding.py` | 消费验证后的上下文约束，保留最终 exact binding |
| P1 | `fault_diagnosis/domain/canonical_turn/contracts.py` | 仅扩展 Canonical 所需审计投影，不塞入原始模型对象 |
| P1 | `fault_diagnosis/agent/canonical_turn/rule_catalog.py` | 演进为统一 `CapabilitySpec` registry |
| P1 | `fault_diagnosis/agent/planning/*` | 改为读取 CapabilitySpec，保持 Validator 权威 |
| P1 | `fault_diagnosis/server/agent_gateway/answer_synthesis.py` | 全量开发模式与统一观测，不改安全边界 |
| P2 | `tests/evals/*` | 扩充到 200+ 语义/上下文案例并增加调用次数、安全和 fallback 断言 |
| P2 | `docs/current-architecture.md`、README | 更新唯一生产链路和配置说明 |

## 10. 明确不做

- 不引入自由工具选择或自由 DAG 规划。
- 不让 LLM 生成 SQL、扩大权限或读取未授权上下文。
- 不让 LLM 直接决定 Artifact ID。
- 不替换现有确定性诊断算法；LLM 辅助根因假设放到后续独立阶段。
- 不让回答模型重新诊断、创建工单、控制设备或修改执行状态。
- 不为当前模型供应商超时扭曲核心架构。

## 11. 执行判断

最短路径不是现在直接把 `.env` 三个开关改成 true；那只会开启旧的“规则主导 + 条件 fallback”，不会得到目标系统。正确的加速方式是先完成 Wave 1 的统一异步语义入口，随后立即在开发环境使用 `primary + context + answer 100%`，用真实交互驱动 Wave 2/3 修正，最后一次性跑完整验收。
