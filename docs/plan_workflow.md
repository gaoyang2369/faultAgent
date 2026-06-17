下面是重新整理后的版本，建议你可以直接给 Codex 作为项目改进说明。

核心调整是：

```text
1. 不再把“维修建议 / 工单”作为一级任务分类。
2. 工单相关逻辑统一作为 workflow 中的 workorder_decision 节点。
3. 复合问题采用 primary_task_type + subgoals 的方式处理。
4. 分类的目标不是给问题贴标签，而是选择 parent workflow、工具权限、证据要求和输出 schema。
```

---

# 一、最终推荐的任务分类

建议一级任务类型分为 8 类：

```text
1. status_query
2. alarm_triage
3. fault_diagnosis
4. root_cause_analysis
5. health_assessment
6. knowledge_qa
7. report_generation
8. action_request
```

其中：

```text
resolution_recommendation
workorder_decision
workorder_draft_generation
```

不作为一级任务类型，而是作为 workflow 中的可选节点。

---

# 二、任务分类总览

| task_type             | 说明                      | 典型问题                         | 是否默认启用工单决策 |
| --------------------- | ----------------------- | ---------------------------- | ---------- |
| `status_query`        | 查询设备当前状态、历史指标、告警状态      | “设备 A 现在正常吗？”“过去 1 小时温度是多少？” | 否，除非发现异常   |
| `alarm_triage`        | 告警分诊：解释告警、判断当前状态、给处理建议  | “E102 是什么意思？现在还有故障吗？怎么处理？”   | 是，条件触发     |
| `fault_diagnosis`     | 对明确异常进行故障诊断             | “设备 A 为什么高温？”“产线 3 为什么停机？”   | 是，条件触发     |
| `root_cause_analysis` | 事故复盘、根因分析、RCA           | “昨天停机根因是什么？”“生成 RCA 报告”      | 可选         |
| `health_assessment`   | 健康度、劣化趋势、风险评估           | “这台设备最近健康吗？”“有没有故障风险？”       | 可选         |
| `knowledge_qa`        | 手册、SOP、告警码、操作步骤问答       | “E102 的定义是什么？”“这个型号怎么校准？”    | 否          |
| `report_generation`   | 基于 evidence bundle 生成报告 | “把这次诊断生成报告”                  | 否，仅引用已有决策  |
| `action_request`      | 涉及写操作、控制操作、确认派单等        | “帮我重启设备”“确认创建工单”             | 必须审批       |

---

# 三、统一任务路由输出结构

Intent Router 不应该只输出一个字符串，而应该输出结构化任务 JSON。

推荐格式：

```json
{
  "primary_task_type": "alarm_triage",
  "route_confidence": 0.88,
  "user_goal": "explain_alarm_check_current_fault_and_recommend_solution",
  "objects": {
    "device_ids": ["pump_001"],
    "alarm_codes": ["E102"],
    "system": null,
    "location": null
  },
  "time_window": {
    "start": null,
    "end": null,
    "is_inferred": false,
    "default_strategy": "current_status"
  },
  "subgoals": [
    {
      "id": "sg_001",
      "type": "explain_alarm_code",
      "required": true,
      "status": "ready"
    },
    {
      "id": "sg_002",
      "type": "check_current_fault_status",
      "required": true,
      "status": "ready"
    },
    {
      "id": "sg_003",
      "type": "recommend_resolution_steps",
      "required": true,
      "status": "ready"
    },
    {
      "id": "sg_004",
      "type": "workorder_decision",
      "required": false,
      "status": "ready"
    }
  ],
  "missing_slots": [],
  "risk_level": "read_only",
  "requested_output": "answer",
  "flags": {
    "need_sql": true,
    "need_knowledge": true,
    "need_analysis": true,
    "need_workorder_decision": true,
    "need_report": false,
    "may_involve_write_action": false
  }
}
```

---

# 四、统一顶层 workflow

所有任务都走统一框架，但不同 `task_type` 会选择不同的 policy 和 enabled nodes。

```text
start
  -> understand / route
      输出 primary_task_type、subgoals、objects、time_window、missing_slots

  -> select_workflow_policy
      选择 parent policy
      选择 subgoal policies
      决定工具白名单、证据要求、输出 schema、风险约束

  -> initialize_evidence_bundle
      初始化任务上下文
      初始化每个 subgoal 的 evidence slots

  -> execute_workflow
      -> collect_asset_context
      -> sql
      -> knowledge
      -> analysis
      -> resolution_recommendation
      -> workorder_decision
      -> report

  -> evidence_validation
      对全局任务和每个 subgoal 分别验证
      判断哪些结论证据充分，哪些只能输出不确定结论

  -> answer_synthesis
      只基于 evidence bundle 生成答案

  -> output_guardrail
      检查越权、幻觉、格式、风险操作、证据缺失

  -> save_artifact
      保存 evidence bundle、诊断结论、报告、工单草稿、审计日志

  -> complete
```

注意顺序建议是：

```text
evidence_validation
  -> answer_synthesis
  -> output_guardrail
  -> save_artifact
```

而不是先 `final_answer` 再做证据校验。

---

# 五、每类任务的 workflow

---

## 1. `status_query`：状态查询

### 适用问题

```text
设备 A 现在正常吗？
设备 A 当前温度是多少？
过去 1 小时有没有告警？
产线 3 当前是否在线？
```

### 目标

回答设备或系统的**当前状态 / 历史状态 / 指标事实**，不主动进行复杂根因诊断。

### workflow

```text
start
  -> understand / route
  -> slot_check
      必需：device_id / system / location 至少一个
      可选：metric、time_window
      缺 time_window 时默认 current 或 last_1h

  -> select_workflow_policy: status_query_policy

  -> initialize_evidence_bundle

  -> sql
      查询设备基础信息
      查询当前在线状态
      查询最近心跳时间
      查询最新关键指标
      查询指标阈值
      查询当前 active alarms
      查询最近告警摘要

  -> analysis
      判断数据是否新鲜
      判断设备是否在线
      判断指标是否越阈
      判断是否存在 active alarm
      输出状态摘要
      如果发现异常，标记可触发 followup_diagnosis 或 workorder_decision

  -> evidence_validation
      检查 device_id 是否明确
      检查数据时间戳是否新鲜
      检查指标单位和阈值是否存在
      检查状态判断是否有数据支撑

  -> answer_synthesis
  -> output_guardrail
  -> save_artifact
  -> complete
```

### 默认节点配置

```json
{
  "task_type": "status_query",
  "enabled_nodes": {
    "sql": true,
    "knowledge": false,
    "analysis": true,
    "resolution_recommendation": false,
    "workorder_decision": "conditional",
    "report": false
  }
}
```

### 工单触发逻辑

`status_query` 默认不启用工单，但如果发现：

```text
设备离线
关键指标严重越阈
存在 active major/critical alarm
异常持续时间超过阈值
```

则可以触发：

```text
workorder_decision = conditional
```

但只能建议或生成草稿，不能自动派发。

---

## 2. `alarm_triage`：告警分诊

### 适用问题

```text
E102 是什么意思？
E102 现在还在发生吗？
这个告警严重吗？
这个告警怎么处理？
这个告警要不要派人？
E102 是什么意思？是不是现在设备有故障？应该如何解决？
```

### 目标

对告警进行一站式分诊：

```text
解释告警含义
判断当前是否 active
判断是否构成真实故障
分析可能原因
给处理建议
判断是否需要工单草稿
```

### workflow

```text
start
  -> understand / route
  -> slot_check
      必需：alarm_code / alarm_name
      条件必需：如果要判断当前状态，则需要 device_id
      缺 device_id 时：
        knowledge 部分可以继续
        current_fault_check 和 workorder_decision 标记 blocked

  -> select_workflow_policy: alarm_triage_policy

  -> initialize_evidence_bundle

  -> knowledge
      查询告警定义
      查询告警等级
      查询触发条件
      查询可能原因
      查询推荐处理步骤
      查询相关 SOP / manual

  -> sql
      如果有 device_id：
        查询该设备当前状态
        查询该告警是否 active
        查询告警发生时间、恢复时间、持续时长
        查询告警前后关键指标
        查询相关联告警
        查询设备最近运行状态
      如果无 device_id：
        跳过实时状态查询

  -> analysis
      解释告警含义
      判断当前是否仍在发生
      判断是否疑似真实故障
      判断可能原因
      判断严重程度
      生成处理建议

  -> resolution_recommendation
      给出排查步骤
      区分立即处理、继续观察、人工巡检

  -> workorder_decision
      如果证据满足条件：
        输出 no_action / monitor / suggest_inspection / generate_workorder_draft / escalate
      如果证据不足：
        输出无法判断派单，需要补充哪些数据

  -> evidence_validation
      检查告警定义是否有知识库来源
      检查当前故障判断是否有实时数据
      检查处理建议是否匹配 SOP
      检查工单建议是否有 active alarm 或严重异常支撑

  -> answer_synthesis
      按子问题合并答案

  -> output_guardrail
  -> save_artifact
  -> complete
```

### 默认节点配置

```json
{
  "task_type": "alarm_triage",
  "enabled_nodes": {
    "sql": "conditional",
    "knowledge": true,
    "analysis": true,
    "resolution_recommendation": true,
    "workorder_decision": "conditional",
    "report": false
  }
}
```

### 典型 subgoals

```json
[
  "explain_alarm_code",
  "check_current_alarm_status",
  "check_current_fault_status",
  "recommend_resolution_steps",
  "workorder_decision"
]
```

---

## 3. `fault_diagnosis`：故障诊断

### 适用问题

```text
设备 A 为什么高温？
设备 A 为什么停机？
产线 3 压力异常，帮我诊断。
这个泵最近经常报警，可能是什么原因？
```

### 目标

基于证据进行故障诊断，输出：

```text
故障现象
关键证据
可能原因
置信度
反证和缺失证据
处理建议
是否建议生成工单
```

### workflow

```text
start
  -> understand / route
  -> slot_check
      必需：device_id / system / location 至少一个
      必需：symptom / alarm / abnormal_metric 至少一个
      可选：time_window
      缺 time_window 时默认 last_2h 或围绕告警时间窗

  -> select_workflow_policy: fault_diagnosis_policy

  -> initialize_evidence_bundle

  -> collect_asset_context
      查询设备类型
      查询设备型号
      查询设备关键性
      查询上下游关系
      查询传感器列表

  -> sql
      查询故障时间窗内关键指标
      查询故障前后趋势
      查询 active / historical alarms
      查询事件日志
      查询设备状态变化
      查询上下游设备状态
      查询最近工单
      查询最近维护记录
      查询最近参数 / 配置 / 版本变更

  -> knowledge
      查询设备手册
      查询告警说明
      查询 FMEA / 故障模式库
      查询 SOP / runbook
      查询历史相似案例

  -> analysis
      构建事件时间线
      识别 first abnormal signal
      识别关键异常指标
      生成候选原因
      为每个候选原因匹配支持证据
      为每个候选原因匹配反驳证据
      计算或标注置信度
      输出 primary cause 和 alternative causes
      输出 missing evidence

  -> resolution_recommendation
      给出下一步排查建议
      给出临时处置建议
      给出需要人工检查的项目

  -> workorder_decision
      根据严重度、持续时间、影响范围、置信度、资产关键性判断是否建议生成工单草稿

  -> report
      仅当用户要求报告时启用

  -> evidence_validation
      检查每个诊断结论是否绑定 evidence
      检查是否区分 symptom、cause、root cause
      检查是否把猜测说成事实
      检查是否列出缺失证据
      检查是否越权建议控制操作

  -> answer_synthesis
  -> output_guardrail
  -> save_artifact
  -> complete
```

### 默认节点配置

```json
{
  "task_type": "fault_diagnosis",
  "enabled_nodes": {
    "sql": true,
    "knowledge": true,
    "analysis": true,
    "resolution_recommendation": true,
    "workorder_decision": true,
    "report": "conditional"
  }
}
```

### 诊断约束

```text
不能直接说“根因是 X”，除非证据满足 root cause 条件。
默认输出“最可能原因 / 疑似原因 / 备选原因”。
每个原因必须绑定 supporting_evidence。
如果缺关键数据，必须降低置信度或输出证据不足。
```

---

## 4. `root_cause_analysis`：根因分析 / RCA

### 适用问题

```text
昨天停机的根因是什么？
帮我复盘这次事故。
生成这次故障的 RCA 报告。
为什么恢复后又复发？
```

### 目标

不是只判断当前故障，而是还原完整事件链路：

```text
事件时间线
影响范围
直接原因
根本原因
诱因 / 放大因素
恢复动作
预防措施
证据和不确定性
```

### workflow

```text
start
  -> understand / route
  -> slot_check
      必需：event_id / device_id / system / time_window 至少有足够定位信息
      RCA 通常必须有明确 time_window

  -> select_workflow_policy: root_cause_analysis_policy

  -> initialize_evidence_bundle

  -> sql
      查询事件开始和结束时间
      查询告警序列
      查询指标趋势
      查询状态变化
      查询上下游影响
      查询人工操作记录
      查询参数 / 配置 / 版本变更记录
      查询工单记录
      查询恢复动作
      查询复发记录

  -> knowledge
      查询故障模式库
      查询历史相似事件
      查询 SOP / 应急预案
      查询影响等级定义
      查询恢复标准

  -> analysis
      构建完整事件时间线
      识别 first abnormal event
      区分 symptom、direct cause、root cause、contributing factor
      评估影响范围
      评估恢复动作是否有效
      判断是否存在复发
      生成预防措施

  -> resolution_recommendation
      输出整改措施
      输出预防性维护建议
      输出监控 / 阈值 / 流程优化建议

  -> workorder_decision
      可选：如果仍有未关闭风险，建议创建整改工单或复查工单

  -> report
      默认启用，生成 RCA 结构化报告

  -> evidence_validation
      检查时间线是否闭合
      检查 root cause 是否满足因果判断条件
      检查是否把相关性写成因果性
      检查是否列出未知项
      检查影响范围是否有数据支撑

  -> answer_synthesis
  -> output_guardrail
  -> save_artifact
  -> complete
```

### 默认节点配置

```json
{
  "task_type": "root_cause_analysis",
  "enabled_nodes": {
    "sql": true,
    "knowledge": true,
    "analysis": true,
    "resolution_recommendation": true,
    "workorder_decision": "conditional",
    "report": true
  }
}
```

### RCA 因果判断规则

只有同时满足下面条件，才能写成 root cause：

```text
1. 时间上早于故障发生
2. 机制上能解释故障
3. 数据上有支持证据
4. 没有强反证
5. 能解释主要影响范围
```

否则只能写成：

```text
suspected cause
contributing factor
correlated event
unverified hypothesis
```

---

## 5. `health_assessment`：健康评估 / 风险评估

### 适用问题

```text
这台设备最近健康吗？
有没有劣化趋势？
未来几天有没有故障风险？
哪些设备风险最高？
```

### 目标

基于长期趋势评估设备健康度和风险，而不是诊断一个已发生故障。

### workflow

```text
start
  -> understand / route
  -> slot_check
      必需：device_id / device_group / system
      可选：assessment_window
      缺时间窗时默认 last_7d 或 last_30d

  -> select_workflow_policy: health_assessment_policy

  -> initialize_evidence_bundle

  -> sql
      查询长期指标趋势
      查询历史告警频次
      查询运行时长
      查询启停次数
      查询维修记录
      查询异常波动
      查询同类设备对比数据
      查询近期工单和缺陷记录

  -> knowledge
      查询健康评分规则
      查询指标阈值
      查询劣化模式
      查询维护周期
      查询设备寿命模型或经验规则

  -> analysis
      趋势分析
      异常检测
      同比 / 环比分析
      同类设备对比
      健康评分
      风险等级判断
      识别主要风险因子

  -> resolution_recommendation
      输出继续观察 / 巡检 / 预防性维护建议

  -> workorder_decision
      如果风险高或关键指标持续劣化，则建议预防性维护工单草稿

  -> report
      用户要求时启用

  -> evidence_validation
      检查健康评分是否有规则来源
      检查趋势判断是否有足够数据点
      检查预测性结论是否标注不确定性
      检查是否误称为确定故障

  -> answer_synthesis
  -> output_guardrail
  -> save_artifact
  -> complete
```

### 默认节点配置

```json
{
  "task_type": "health_assessment",
  "enabled_nodes": {
    "sql": true,
    "knowledge": "conditional",
    "analysis": true,
    "resolution_recommendation": true,
    "workorder_decision": "conditional",
    "report": "conditional"
  }
}
```

---

## 6. `knowledge_qa`：知识问答 / SOP 问答

### 适用问题

```text
E102 的定义是什么？
这个型号怎么校准？
如何更换滤芯？
这个设备维护周期是多少？
```

### 目标

基于知识库、手册、SOP 回答通用知识或操作步骤。

### workflow

```text
start
  -> understand / route
  -> slot_check
      必需：alarm_code / model / topic / operation 至少一个
      如果用户绑定具体设备，则需要 device_id

  -> select_workflow_policy: knowledge_qa_policy

  -> initialize_evidence_bundle

  -> knowledge
      查询手册
      查询 SOP
      查询告警定义
      查询操作步骤
      查询安全注意事项
      查询版本适配说明

  -> sql
      仅当用户问题绑定具体设备时启用：
        查询设备型号
        查询设备版本
        查询当前状态
        查询当前告警

  -> analysis
      整理适用范围
      整理步骤
      检查前置条件
      检查安全风险
      标注版本 / 型号限制

  -> resolution_recommendation
      仅当用户问“怎么处理”时启用

  -> workorder_decision
      默认不启用

  -> report
      默认不启用

  -> evidence_validation
      检查知识来源
      检查型号和版本是否匹配
      检查高风险操作是否有安全提示
      检查是否误用不匹配手册

  -> answer_synthesis
  -> output_guardrail
  -> save_artifact
  -> complete
```

### 默认节点配置

```json
{
  "task_type": "knowledge_qa",
  "enabled_nodes": {
    "sql": "conditional",
    "knowledge": true,
    "analysis": true,
    "resolution_recommendation": "conditional",
    "workorder_decision": false,
    "report": false
  }
}
```

---

## 7. `report_generation`：报告生成

### 适用问题

```text
把这次诊断生成报告。
生成 RCA 报告。
生成今天设备状态日报。
把刚才的分析整理成工单说明。
```

### 目标

基于已有 evidence bundle 或补充查询后的 evidence bundle 生成报告。

### workflow

```text
start
  -> understand / route
  -> slot_check
      必需：report_type
      条件必需：event_id / device_id / time_window / existing_evidence_bundle_id

  -> select_workflow_policy: report_generation_policy

  -> initialize_or_load_evidence_bundle
      优先加载已有 evidence bundle
      如果没有，则根据 report_type 决定是否补充查询

  -> sql
      条件启用：
        如果用户要求最新数据
        如果 evidence bundle 缺少必要数据
        如果是日报 / 周报

  -> knowledge
      条件启用：
        查询报告模板
        查询 SOP
        查询 RCA 模板
        查询评级标准

  -> analysis
      整理结论
      整理证据
      整理时间线
      整理影响范围
      整理建议和限制

  -> report
      按模板生成结构化报告

  -> evidence_validation
      检查报告里的每个关键结论是否有 evidence
      检查是否遗漏限制条件
      检查是否混入未验证猜测
      检查报告时间窗是否明确

  -> answer_synthesis
      输出报告摘要和报告正文

  -> output_guardrail
  -> save_artifact
      保存 report artifact
  -> complete
```

### 默认节点配置

```json
{
  "task_type": "report_generation",
  "enabled_nodes": {
    "sql": "conditional",
    "knowledge": "conditional",
    "analysis": true,
    "resolution_recommendation": "conditional",
    "workorder_decision": false,
    "report": true
  }
}
```

---

## 8. `action_request`：操作请求 / 写操作请求

### 适用问题

```text
帮我重启设备。
关闭这个告警。
把阈值改成 90。
确认创建工单。
派发这个工单。
```

### 目标

识别和处理涉及写操作、控制操作、状态变更的请求。

### 重要原则

`action_request` 是最高风险类型，必须走权限和审批。

初期建议只支持：

```text
生成草稿
请求确认
输出审批提示
拒绝高风险操作
```

不要直接执行设备控制。

### workflow

```text
start
  -> understand / route
  -> identify_action_type
      action_type:
        create_workorder
        dispatch_workorder
        update_config
        acknowledge_alarm
        restart_device
        stop_device
        close_alarm
        other_write_action

  -> permission_check
      检查用户身份
      检查角色权限
      检查是否允许该动作
      检查是否需要审批

  -> risk_check
      判断低 / 中 / 高 / 极高风险
      高风险动作必须 human confirmation
      极高风险动作直接拒绝或升级

  -> select_workflow_policy: action_request_policy

  -> initialize_evidence_bundle

  -> sql
      查询设备当前状态
      查询当前告警
      查询是否已有工单
      查询操作前置条件
      查询安全状态

  -> knowledge
      查询 SOP
      查询安全限制
      查询操作前置条件
      查询审批规则

  -> analysis
      判断操作是否合理
      判断是否满足前置条件
      判断风险和影响
      判断是否需要审批

  -> action_decision
      deny
      require_more_evidence
      require_confirmation
      create_draft_only
      escalate_to_human
      execute_if_allowed

  -> evidence_validation
      检查是否有权限
      检查是否有足够证据
      检查是否违反安全规则
      检查是否绕过审批

  -> answer_synthesis
  -> output_guardrail
  -> audit_log
  -> complete
```

### 默认节点配置

```json
{
  "task_type": "action_request",
  "enabled_nodes": {
    "sql": true,
    "knowledge": true,
    "analysis": true,
    "resolution_recommendation": true,
    "workorder_decision": "conditional",
    "report": false,
    "permission_check": true,
    "risk_check": true,
    "audit_log": true
  }
}
```

### 高风险限制

```text
重启设备
停机
修改阈值
关闭告警
屏蔽告警
修改控制参数
绕过联锁
强制运行
```

这些操作不能由 agent 直接执行，必须走审批或人工确认。

---

# 六、工单逻辑统一作为 workflow node

不建议把工单作为一级分类。

推荐统一抽象为：

```text
workorder_decision
```

该节点只负责判断：

```text
是否需要工单
是否需要巡检
是否需要升级
是否生成工单草稿
工单优先级
工单理由
```

不直接派发。

---

## `workorder_decision` 通用流程

```text
workorder_decision
  -> check_fault_status
      active / recovered / intermittent / unknown

  -> check_severity
      info / minor / major / critical

  -> check_duration
      异常持续时间

  -> check_asset_criticality
      普通设备 / 关键设备 / 安全相关设备

  -> check_business_impact
      是否影响产线 / 质量 / 安全 / SLA

  -> check_confidence
      诊断置信度是否足够

  -> check_existing_workorder
      是否已有未关闭工单

  -> decide
      no_action
      monitor
      suggest_inspection
      suggest_workorder
      generate_workorder_draft
      escalate_to_human
```

---

## 工单决策输出 schema

```json
{
  "workorder_decision": {
    "decision": "generate_workorder_draft",
    "priority": "P2",
    "requires_human_confirmation": true,
    "reason": "设备 pump_001 的 E102 高温告警仍处于 active 状态，温度连续 18 分钟超过高阈值，且该设备属于关键产线设备。",
    "evidence_ids": ["ev_alarm_001", "ev_metric_003", "ev_asset_002"],
    "draft": {
      "title": "pump_001 E102 高温告警检查",
      "description": "设备 pump_001 在 09:42 触发 E102 高温告警，温度持续高于阈值，建议检查冷却系统、风扇、负载和温度传感器。",
      "priority": "P2",
      "recommended_assignee_role": "maintenance_engineer"
    }
  }
}
```

---

# 七、复合问题处理方式

复合问题不要强行压成单个任务标签，而是：

```text
primary_task_type + subgoals
```

例如用户问：

```text
这个 E102 故障码是什么意思？是不是现在设备有故障？应该如何来解决？
```

应该解析成：

```json
{
  "primary_task_type": "alarm_triage",
  "subgoals": [
    {
      "id": "sg_001",
      "type": "explain_alarm_code",
      "status": "ready"
    },
    {
      "id": "sg_002",
      "type": "check_current_fault_status",
      "status": "blocked",
      "missing_slots": ["device_id"]
    },
    {
      "id": "sg_003",
      "type": "recommend_resolution_steps",
      "status": "ready"
    },
    {
      "id": "sg_004",
      "type": "workorder_decision",
      "status": "blocked",
      "missing_slots": ["device_id", "current_alarm_status"]
    }
  ]
}
```

如果用户补充了设备：

```text
pump_001 的 E102 故障码是什么意思？是不是现在设备有故障？应该如何解决？
```

则变成：

```json
{
  "primary_task_type": "alarm_triage",
  "objects": {
    "device_ids": ["pump_001"],
    "alarm_codes": ["E102"]
  },
  "subgoals": [
    {
      "id": "sg_001",
      "type": "explain_alarm_code",
      "status": "ready"
    },
    {
      "id": "sg_002",
      "type": "check_current_fault_status",
      "status": "ready"
    },
    {
      "id": "sg_003",
      "type": "recommend_resolution_steps",
      "status": "ready"
    },
    {
      "id": "sg_004",
      "type": "workorder_decision",
      "status": "ready"
    }
  ]
}
```

---

## 复合任务执行原则

```text
1. 选择一个 parent workflow。
2. 在 parent workflow 下执行多个 subgoals。
3. ready 的 subgoal 先执行。
4. blocked 的 subgoal 不阻塞整个任务，但要说明缺什么。
5. 不要简单合并多个 policy 的工具权限。
6. 每个 subgoal 只允许使用自己的局部工具白名单。
7. 最终答案按 subgoal 分段输出。
```

---

# 八、Policy 设计建议

每类任务对应一个 parent policy。

示例：

```json
{
  "policy_id": "alarm_triage_v1",
  "task_type": "alarm_triage",
  "workflow_id": "wf_alarm_triage_v1",
  "required_slots": [
    "alarm_code"
  ],
  "conditional_required_slots": {
    "check_current_fault_status": ["device_id"],
    "workorder_decision": ["device_id", "current_alarm_status"]
  },
  "allowed_tools": [
    "knowledge_base.search",
    "asset_db.read",
    "timeseries_db.read",
    "alarm_db.read",
    "event_log.read",
    "workorder_db.read"
  ],
  "forbidden_tools": [
    "device_control.write",
    "config.write",
    "workorder.dispatch"
  ],
  "enabled_nodes": {
    "knowledge": true,
    "sql": "conditional",
    "analysis": true,
    "resolution_recommendation": true,
    "workorder_decision": "conditional",
    "report": false
  },
  "evidence_requirements": {
    "need_alarm_definition": true,
    "need_alarm_severity": true,
    "need_recommended_actions": true,
    "need_current_alarm_status_if_device_provided": true,
    "need_metric_context_if_claiming_current_fault": true
  },
  "output_schema": "alarm_triage_answer_v1",
  "on_missing_evidence": "answer_available_subgoals_and_mark_blocked_subgoals",
  "guardrails": [
    "no_current_fault_claim_without_realtime_data",
    "no_workorder_dispatch_without_human_confirmation",
    "show_uncertainty",
    "cite_evidence_ids"
  ]
}
```

---

# 九、Evidence Bundle 结构建议

所有 workflow 都应该写入统一 evidence bundle。

```json
{
  "bundle_id": "eb_20260617_001",
  "task": {
    "primary_task_type": "alarm_triage",
    "subgoals": ["sg_001", "sg_002", "sg_003", "sg_004"],
    "objects": {
      "device_ids": ["pump_001"],
      "alarm_codes": ["E102"]
    },
    "time_window": {
      "start": "2026-06-17T09:00:00",
      "end": "2026-06-17T10:00:00"
    }
  },
  "evidence_items": [
    {
      "evidence_id": "ev_001",
      "source_type": "knowledge_base",
      "content_type": "alarm_definition",
      "content": {
        "alarm_code": "E102",
        "meaning": "high_temperature_alarm",
        "severity": "major"
      },
      "quality": {
        "freshness": "static",
        "reliability": "high"
      },
      "supports_subgoals": ["sg_001", "sg_003"]
    },
    {
      "evidence_id": "ev_002",
      "source_type": "alarm_db",
      "content_type": "current_alarm_status",
      "content": {
        "device_id": "pump_001",
        "alarm_code": "E102",
        "status": "active",
        "start_time": "2026-06-17T09:42:00"
      },
      "quality": {
        "freshness": "fresh",
        "reliability": "high"
      },
      "supports_subgoals": ["sg_002", "sg_004"]
    }
  ],
  "claims": [
    {
      "claim_id": "claim_001",
      "claim": "E102 表示高温告警。",
      "claim_type": "alarm_explanation",
      "supporting_evidence": ["ev_001"],
      "confidence": "high"
    },
    {
      "claim_id": "claim_002",
      "claim": "pump_001 当前存在 active E102 告警。",
      "claim_type": "current_fault_status",
      "supporting_evidence": ["ev_002"],
      "confidence": "high"
    }
  ],
  "missing_evidence": [
    {
      "subgoal_id": "sg_002",
      "missing": "temperature_timeseries",
      "impact": "无法判断高温是否持续恶化"
    }
  ]
}
```

---

# 十、Evidence Validation 通用规则

```text
1. 所有结论必须绑定 evidence_id。
2. 当前状态判断必须有实时数据或最近数据。
3. 故障诊断结论必须有 supporting_evidence。
4. RCA 根因必须满足因果判断条件。
5. 工单建议必须有异常严重度、持续时间或风险证据。
6. 缺少设备时不能判断当前设备是否故障。
7. 缺少时间窗时不能做历史趋势或 RCA。
8. 知识问答必须检查型号、版本、适用范围。
9. 高风险操作必须检查权限和审批。
10. 不允许把 suspected cause 写成 confirmed root cause。
```

---

# 十一、最终分类决策规则

可以先使用规则 + LLM 混合路由。

## 强规则优先

```text
包含“报告 / 生成报告 / 总结成文档”
  -> report_generation

包含“RCA / 根因分析 / 复盘 / 事故分析”
  -> root_cause_analysis

包含“重启 / 停机 / 修改 / 关闭 / 确认派发 / 创建工单”
  -> action_request

包含“健康 / 风险 / 劣化 / 趋势 / 预测”
  -> health_assessment

包含“故障码 / 告警码 / 报警 / 告警”
  且包含“现在 / 是否故障 / 怎么处理 / 严重吗 / 要不要派人”
  -> alarm_triage

包含“故障码是什么意思 / 告警定义 / 手册 / SOP / 怎么操作”
  且不绑定具体设备状态
  -> knowledge_qa

包含“为什么 / 原因 / 诊断 / 异常 / 故障”
  -> fault_diagnosis

包含“现在 / 当前 / 多少 / 是否在线 / 状态”
  -> status_query
```

## 模糊问题处理

如果路由置信度低：

```text
route_confidence < 0.6
```

不要直接进入完整诊断，优先进入轻量分诊：

```text
status_query 或 alarm_triage
```

并允许后续升级为：

```text
fault_diagnosis
root_cause_analysis
health_assessment
```

---

# 十二、最终建议的工程结构

可以让 Codex 按下面结构改造：

```text
src/
  agent/
    router/
      query_normalizer.ts
      intent_router.ts
      subgoal_decomposer.ts

    policies/
      status_query.policy.ts
      alarm_triage.policy.ts
      fault_diagnosis.policy.ts
      root_cause_analysis.policy.ts
      health_assessment.policy.ts
      knowledge_qa.policy.ts
      report_generation.policy.ts
      action_request.policy.ts

    workflows/
      workflow_executor.ts
      status_query.workflow.ts
      alarm_triage.workflow.ts
      fault_diagnosis.workflow.ts
      root_cause_analysis.workflow.ts
      health_assessment.workflow.ts
      knowledge_qa.workflow.ts
      report_generation.workflow.ts
      action_request.workflow.ts

    evidence/
      evidence_bundle.ts
      evidence_store.ts
      evidence_validator.ts
      claim_validator.ts

    nodes/
      sql_node.ts
      knowledge_node.ts
      analysis_node.ts
      resolution_recommendation_node.ts
      workorder_decision_node.ts
      report_node.ts
      permission_check_node.ts
      risk_check_node.ts

    output/
      answer_synthesizer.ts
      output_guardrail.ts
      schemas/
        status_query_answer.schema.ts
        alarm_triage_answer.schema.ts
        fault_diagnosis_answer.schema.ts
        rca_answer.schema.ts
        health_assessment_answer.schema.ts
        knowledge_qa_answer.schema.ts
        report_answer.schema.ts
        action_request_answer.schema.ts
```

---

# 十三、最终一句话版本

新的分类方式可以总结为：

```text
一级分类只负责选择 parent workflow：
status_query、alarm_triage、fault_diagnosis、root_cause_analysis、
health_assessment、knowledge_qa、report_generation、action_request。

维修建议、解决方案、工单判断不作为一级分类，
而是作为 resolution_recommendation 和 workorder_decision 节点，
由诊断、告警分诊、状态评估、健康评估等 workflow 在证据满足时触发。

复合问题不做单标签分类，
而是解析成 primary_task_type + subgoals，
ready 的子目标先执行，blocked 的子目标说明缺失信息，
最终按子目标合成答案。
```
