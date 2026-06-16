你的整体方向是对的：**单 agent 不代表一个自由发挥的大 workflow，而是“一个 agent + 多个固定 workflow + policy 约束 + evidence 驱动输出”**。

我建议分类时不要按“要不要查 SQL / 要不要查知识库”来分，而是按**用户任务目标**来分。SQL、知识库、分析、报告只是每类任务里的可选步骤，由 policy 控制。

---

## 一、推荐的任务分类体系

可以分成两层：

第一层是 **主任务类型 task_type**，决定走哪个固定 workflow。

第二层是 **任务修饰信息 modifiers**，决定 workflow 里的工具、证据要求、风险等级和输出格式。

### 1. 主任务类型

| task_type                          | 典型用户问题                        | 核心目标                  |
| ---------------------------------- | ----------------------------- | --------------------- |
| `status_query` 状态查询                | “设备 A 现在正常吗？”“过去 1 小时温度是多少？”  | 查询当前状态、指标、告警、在线情况     |
| `alarm_interpretation` 告警解释        | “这个 E102 告警是什么意思？”“为什么一直报高温？” | 解释告警含义、影响、可能原因        |
| `fault_diagnosis` 故障诊断             | “设备 A 为什么停机？”“产线 3 压力异常，帮我诊断” | 基于证据推断可能原因            |
| `root_cause_analysis` 根因分析 / 复盘    | “昨天那次停机根因是什么？”“生成 RCA”        | 还原事件链路，输出根因、影响和改进措施   |
| `health_assessment` 健康评估 / 趋势异常    | “这台设备最近健康吗？”“有没有劣化趋势？”        | 基于长期趋势评估风险            |
| `maintenance_advice` 维修建议 / 派单决策   | “要不要派工单？”“下一步怎么处理？”           | 判断是否需要人工介入、工单优先级、处理建议 |
| `knowledge_qa` 知识问答 / 操作指导         | “这个型号怎么校准？”“更换滤芯步骤是什么？”       | 从手册、SOP、知识库回答         |
| `report_generation` 报告生成           | “把这次诊断生成报告”                   | 基于已有 evidence 生成结构化报告 |
| `config_change_impact` 配置 / 变更影响分析 | “刚改了阈值，会不会导致误报？”              | 分析配置变更、版本变更、参数变更的影响   |
| `action_request` 操作请求              | “帮我重启设备”“关闭这个告警”              | 涉及写操作或控制操作，必须强权限与审批   |

其中前 8 类是故障诊断 agent 的核心。`config_change_impact` 和 `action_request` 可以后续扩展，但建议一开始就预留，因为真实故障诊断里经常会涉及“最近有没有改过配置 / 参数 / 程序”。

---

## 二、Intent Router 不应该只输出 task_type

你的 `Query Normalizer / Intent Router` 输出最好是一个**可执行任务 JSON**，而不是简单分类标签。

建议结构如下：

```json
{
  "task_type": "fault_diagnosis",
  "sub_type": "single_device_fault",
  "user_goal": "diagnose_cause",
  "objects": {
    "device_ids": ["pump_001"],
    "system": "cooling_system",
    "location": "line_3"
  },
  "symptoms": [
    {
      "name": "temperature_high",
      "raw_text": "温度过高"
    }
  ],
  "alarms": [
    {
      "alarm_code": "E102",
      "alarm_name": "high_temperature"
    }
  ],
  "time_window": {
    "start": "2026-06-16T08:00:00",
    "end": "2026-06-16T10:00:00",
    "is_inferred": true
  },
  "requested_output": "diagnosis",
  "urgency": "medium",
  "risk_level": "read_only",
  "missing_slots": [],
  "route_confidence": 0.86,
  "secondary_flags": {
    "need_sql": true,
    "need_knowledge": true,
    "need_trend_analysis": true,
    "need_workorder_decision": true,
    "need_report": false,
    "may_involve_control_action": false
  }
}
```

这里最重要的是：**task_type 决定 workflow，secondary_flags 决定 workflow 内哪些节点启用。**

---

## 三、每一类任务的 workflow 设计

你给的总体流程可以保留：

```text
start
  -> understand / route
  -> select_workflow_policy
  -> initialize_evidence_bundle
  -> execute_workflow
      -> sql
      -> knowledge
      -> analysis
      -> workorder_decision
      -> report
  -> evidence_validation
  -> final_answer
  -> output_guardrail
  -> save_artifact
  -> complete
```

但每类任务要有自己的固定子流程。

---

# 1. 状态查询：`status_query`

### 适用问题

用户问：

> “设备 A 正常吗？”
> “现在压力多少？”
> “过去 30 分钟有没有告警？”
> “产线 3 当前运行状态？”

### 主要风险

这类任务看似简单，但容易出现两个问题：

1. 用户没说时间窗；
2. agent 把“当前状态”解释成“诊断结论”。

状态查询只应该回答**状态事实**，不要过度诊断。

### workflow

```text
start
  -> understand / route
  -> check_required_slots
      必需：设备 / 系统 / 指标
      可选：时间窗
  -> select_workflow_policy: status_query_policy
  -> initialize_evidence_bundle
  -> sql
      查询设备在线状态
      查询关键实时指标
      查询最近告警
      查询最近心跳 / 数据更新时间
  -> analysis
      判断数据是否新鲜
      判断指标是否越阈
      汇总当前状态
  -> evidence_validation
      检查是否有设备
      检查数据时间戳
      检查单位和阈值来源
  -> final_answer
  -> output_guardrail
  -> complete
```

### SQL 证据

状态查询至少需要：

```text
device_status
latest_metrics
latest_alarm
last_seen_time
thresholds
```

### 输出 schema

```json
{
  "answer_type": "status_query",
  "device": "pump_001",
  "status": "abnormal",
  "summary": "设备在线，但温度超过高阈值。",
  "key_metrics": [
    {
      "name": "temperature",
      "value": 92.5,
      "unit": "°C",
      "threshold": 85,
      "status": "high",
      "timestamp": "2026-06-16T10:00:00"
    }
  ],
  "alarms": [],
  "data_freshness": "fresh",
  "limitations": []
}
```

---

# 2. 告警解释：`alarm_interpretation`

### 适用问题

> “E102 是什么告警？”
> “这个高温告警严重吗？”
> “这个告警为什么频繁出现？”

### 关键点

告警解释有两种：

| 类型     | 说明                    |
| ------ | --------------------- |
| 静态解释   | 只解释告警码、含义、等级、处理建议     |
| 结合现场解释 | 结合设备当前数据、历史趋势、上下游状态分析 |

所以这个 workflow 要由 policy 决定是否查 SQL。

### workflow

```text
start
  -> understand / route
  -> check_required_slots
      必需：alarm_code 或 alarm_name
      可选：device_id、time_window
  -> select_workflow_policy: alarm_interpretation_policy
  -> initialize_evidence_bundle
  -> knowledge
      查询告警定义
      查询告警等级
      查询可能原因
      查询推荐处理步骤
  -> sql   如果有具体设备 / 时间窗
      查询该告警发生时间
      查询发生频率
      查询告警前后关键指标
      查询同时间段相关告警
  -> analysis
      静态解释
      结合设备数据判断是否疑似真实故障 / 误报 / 传感器异常
  -> workorder_decision
      如果告警等级高或持续存在，则建议派单
  -> evidence_validation
  -> final_answer
  -> output_guardrail
  -> complete
```

### 证据要求

```text
alarm_definition 必须有
alarm_severity 必须有
recommended_actions 必须有
device_context 有设备时必须有
alarm_occurrence 有设备时必须有
```

### 输出建议

```text
告警含义
严重程度
当前是否仍在发生
可能原因
建议检查项
是否建议派单
证据来源
不确定性说明
```

---

# 3. 故障诊断：`fault_diagnosis`

这是最核心的一类。

### 适用问题

> “设备 A 为什么停机？”
> “温度一直上升，帮我诊断。”
> “产线 3 压力不稳定，可能是什么原因？”
> “这个泵最近经常报警，分析一下。”

### 关键原则

故障诊断 workflow 一定不能让 LLM 直接猜原因。

应该走：

```text
症状识别
  -> 证据采集
  -> 候选原因生成
  -> 候选原因验证
  -> 置信度排序
  -> 缺证据说明
  -> 下一步建议
```

### workflow

```text
start
  -> understand / route
  -> check_required_slots
      必需：设备 / 系统 / 故障现象 / 时间窗 至少满足其中关键组合
      缺设备：追问或引导选择范围
      缺时间窗：使用默认时间窗，但标记为 inferred
  -> select_workflow_policy: fault_diagnosis_policy
  -> initialize_evidence_bundle

  -> sql
      查询故障时间窗内关键指标
      查询故障前后趋势
      查询告警序列
      查询事件日志
      查询设备状态变化
      查询上下游设备状态
      查询最近工单
      查询最近配置 / 参数 / 版本变更

  -> knowledge
      查询设备手册
      查询告警说明
      查询 FMEA / 故障模式库
      查询 SOP / runbook
      查询历史相似案例

  -> analysis
      构建事件时间线
      识别异常指标
      生成候选原因
      针对每个候选原因匹配支持 / 反驳证据
      计算置信度
      输出最可能原因、备选原因、缺失证据

  -> workorder_decision
      判断是否需要派单
      判断优先级
      判断推荐处理动作
      判断是否需要升级给专家

  -> report 可选
      如果用户要求报告，生成结构化诊断报告

  -> evidence_validation
      检查每个诊断结论是否有 evidence 支撑
      检查是否使用了过期数据
      检查是否存在越权建议
      检查是否把猜测说成事实

  -> final_answer
  -> output_guardrail
  -> save_artifact
  -> complete
```

### 故障诊断 evidence bundle

建议统一成这种结构：

```json
{
  "task": {
    "task_type": "fault_diagnosis",
    "device_ids": ["pump_001"],
    "time_window": {
      "start": "2026-06-16T08:00:00",
      "end": "2026-06-16T10:00:00"
    },
    "symptom": "temperature_high"
  },
  "observations": [
    {
      "id": "obs_001",
      "type": "metric",
      "name": "temperature",
      "value": "92.5°C",
      "timestamp": "2026-06-16T09:45:00",
      "source": "timeseries_db",
      "quality": "good"
    }
  ],
  "events": [
    {
      "id": "evt_001",
      "type": "alarm",
      "alarm_code": "E102",
      "severity": "major",
      "timestamp": "2026-06-16T09:47:00",
      "source": "alarm_db"
    }
  ],
  "knowledge": [
    {
      "id": "kb_001",
      "type": "manual",
      "title": "Pump high temperature troubleshooting",
      "snippet": "High temperature may be caused by cooling failure, overload, bearing wear..."
    }
  ],
  "hypotheses": [
    {
      "id": "h_001",
      "cause": "冷却系统异常",
      "supporting_evidence": ["obs_001", "evt_001", "kb_001"],
      "contradicting_evidence": [],
      "confidence": 0.78
    }
  ],
  "evidence_gaps": [
    {
      "gap": "缺少冷却水流量数据",
      "impact": "无法确认冷却系统是否为根因"
    }
  ]
}
```

### 诊断输出 schema

```json
{
  "answer_type": "fault_diagnosis",
  "summary": "设备 pump_001 在 09:45 左右出现温度异常升高，最可能原因是冷却系统异常。",
  "primary_cause": {
    "name": "冷却系统异常",
    "confidence": "medium_high",
    "reasoning": "温度持续升高，同时出现高温告警，且故障手册中该模式与冷却不足高度相关。"
  },
  "alternative_causes": [
    {
      "name": "轴承磨损",
      "confidence": "medium",
      "missing_evidence": "缺少振动数据"
    },
    {
      "name": "负载过高",
      "confidence": "low",
      "missing_evidence": "缺少负载电流趋势"
    }
  ],
  "recommended_next_steps": [
    "检查冷却水流量",
    "检查散热风扇状态",
    "查看轴承振动数据"
  ],
  "workorder_decision": {
    "need_workorder": true,
    "priority": "P2",
    "reason": "温度持续超过阈值，存在停机风险。"
  },
  "limitations": [
    "当前缺少冷却水流量和振动数据，因此根因置信度不能判定为 high。"
  ]
}
```

---

# 4. 根因分析 / 复盘：`root_cause_analysis`

### 适用问题

> “昨天停机的根因是什么？”
> “生成这次事故的 RCA。”
> “这次故障影响范围多大？”
> “为什么恢复后又复发？”

### 和故障诊断的区别

`fault_diagnosis` 主要回答：

> 当前问题可能是什么原因？

`root_cause_analysis` 回答：

> 已发生事件的完整链路是什么？根因、诱因、影响、恢复、预防措施分别是什么？

RCA 更强调时间线和责任链路。

### workflow

```text
start
  -> understand / route
  -> check_required_slots
      必需：事件 / 时间窗 / 设备或系统范围
  -> select_workflow_policy: rca_policy
  -> initialize_evidence_bundle

  -> sql
      查询事件开始 / 结束时间
      查询告警序列
      查询状态变化
      查询指标趋势
      查询上下游影响
      查询人工操作记录
      查询配置变更记录
      查询工单和恢复动作

  -> knowledge
      查询故障模式库
      查询历史相似事故
      查询 SOP / 应急预案
      查询 SLA / 影响等级定义

  -> analysis
      构建事件时间线
      识别 first abnormal signal
      区分直接原因、根本原因、诱因、放大因素
      计算影响范围
      评估恢复措施是否有效
      生成预防措施

  -> report
      生成 RCA 报告

  -> evidence_validation
      检查时间线是否闭合
      检查根因是否有证据
      检查是否把相关性误写成因果性
      检查是否缺少关键日志

  -> final_answer
  -> output_guardrail
  -> save_artifact
  -> complete
```

### RCA 输出结构

```text
1. 事件摘要
2. 影响范围
3. 事件时间线
4. 直接原因
5. 根本原因
6. 诱因 / 促成因素
7. 已采取恢复动作
8. 后续整改措施
9. 未确认事项
10. 证据清单
```

### RCA 的关键 guardrail

RCA 里最容易犯的错误是：

> 看到 A 和 B 同时发生，就说 A 导致了 B。

所以可以加一个规则：

```text
只有当某个原因同时满足：
1. 时间上早于故障；
2. 机制上能解释故障；
3. 数据上有支持；
4. 没有强反证；
才能被写成 root cause。
否则只能写成 suspected cause 或 contributing factor。
```

---

# 5. 健康评估 / 趋势异常：`health_assessment`

### 适用问题

> “这台设备最近健康吗？”
> “有没有劣化趋势？”
> “预测一下会不会故障。”
> “哪些设备风险最高？”

### 重点

这类任务不一定有明确故障，而是做**风险评估**。

### workflow

```text
start
  -> understand / route
  -> check_required_slots
      必需：设备 / 设备组 / 指标范围
      可选：评估周期
  -> select_workflow_policy: health_assessment_policy
  -> initialize_evidence_bundle

  -> sql
      查询长期指标趋势
      查询历史告警频次
      查询维修记录
      查询运行时长
      查询启停次数
      查询异常波动
      查询同类设备对比数据

  -> knowledge
      查询健康评分规则
      查询阈值
      查询劣化模式
      查询维护周期

  -> analysis
      趋势分析
      异常检测
      同比 / 环比 / 同类设备对比
      健康评分
      风险等级判断

  -> workorder_decision
      如果风险高，建议预防性维护
      如果风险中，建议观察或补充检测
      如果风险低，建议继续监控

  -> report 可选

  -> evidence_validation
  -> final_answer
  -> output_guardrail
  -> complete
```

### 输出结构

```json
{
  "answer_type": "health_assessment",
  "health_score": 68,
  "risk_level": "medium",
  "summary": "设备整体可运行，但温度和振动指标出现轻微劣化趋势。",
  "risk_factors": [
    {
      "factor": "温度 7 日均值上升",
      "severity": "medium",
      "evidence": "过去 7 天均值从 73°C 上升到 81°C"
    }
  ],
  "recommendation": "建议继续监控，并在下次巡检中检查冷却系统和轴承状态。",
  "workorder_decision": {
    "need_workorder": false,
    "reason": "当前未超过停机阈值，建议观察。"
  }
}
```

---

# 6. 维修建议 / 派单决策：`maintenance_advice`

### 适用问题

> “要不要派单？”
> “这个问题怎么处理？”
> “下一步该检查什么？”
> “是否需要停机？”

### 关键点

这类任务的核心不是“查原因”，而是**做决策**。

但决策必须依赖诊断证据，不能凭空建议。

### workflow

```text
start
  -> understand / route
  -> check_required_slots
      必需：设备 / 问题 / 当前状态
  -> select_workflow_policy: maintenance_advice_policy
  -> initialize_evidence_bundle

  -> sql
      查询当前状态
      查询故障严重度
      查询告警持续时间
      查询历史工单
      查询 SLA / 影响范围

  -> knowledge
      查询维修 SOP
      查询安全规则
      查询派单规则
      查询备件要求
      查询停机条件

  -> analysis
      判断是否需要人工介入
      判断是否需要立即处理
      判断是否可以继续观察
      判断是否需要停机
      判断工单优先级

  -> workorder_decision
      输出 create / update / no_action / escalate
      如果有写权限，只能生成草稿或等待确认

  -> evidence_validation
      决策必须有证据
      高风险操作必须检查权限和审批
  -> final_answer
  -> output_guardrail
  -> complete
```

### 派单决策规则示例

```text
P1：存在安全风险、设备停机、核心产线中断、关键指标严重越限
P2：持续异常、可能导致停机、重复告警、高风险设备
P3：轻微异常、短时恢复、建议巡检
P4：信息记录、观察项、无需立即处理
```

### 输出结构

```json
{
  "answer_type": "maintenance_advice",
  "decision": "create_workorder",
  "priority": "P2",
  "reason": "高温告警持续 18 分钟，温度超过阈值 7.5°C，存在停机风险。",
  "recommended_actions": [
    "检查冷却水流量",
    "检查风扇运行状态",
    "确认温度传感器读数是否可信"
  ],
  "safety_notes": [
    "如温度继续升高，应按 SOP 执行降载或停机流程。"
  ],
  "requires_confirmation": true
}
```

---

# 7. 知识问答 / 操作指导：`knowledge_qa`

### 适用问题

> “这个型号怎么校准？”
> “E102 的处理步骤是什么？”
> “怎么更换滤芯？”
> “这台设备维护周期是多少？”

### 重点

知识问答应该和实际设备诊断分开。

如果用户只问手册知识，不要强行查 SQL。

如果用户问“我的这台设备怎么处理”，则需要查 SQL。

### workflow

```text
start
  -> understand / route
  -> check_required_slots
      必需：设备型号 / 告警码 / 操作主题
  -> select_workflow_policy: knowledge_qa_policy
  -> initialize_evidence_bundle

  -> knowledge
      查询手册
      查询 SOP
      查询告警说明
      查询安全注意事项
      查询版本适配信息

  -> sql 可选
      如果问题绑定具体设备，则查询设备型号 / 当前状态 / 当前告警

  -> analysis
      整理步骤
      检查适用条件
      检查安全风险
      标出不可确认信息

  -> evidence_validation
      检查知识来源
      检查型号 / 版本是否匹配
      高风险步骤加安全提示
  -> final_answer
  -> output_guardrail
  -> complete
```

### 输出结构

```text
适用对象
操作前置条件
操作步骤
安全注意事项
异常情况处理
资料来源 / 版本
```

---

# 8. 报告生成：`report_generation`

### 适用问题

> “生成诊断报告。”
> “把刚才的结果整理成报告。”
> “输出日报 / 周报。”
> “生成工单说明。”

### 关键点

报告生成不应该重新自由发挥，而应该**基于已有 evidence bundle**。

如果 evidence 不完整，报告里必须写“不完整”。

### workflow

```text
start
  -> understand / route
  -> select_workflow_policy: report_generation_policy
  -> initialize_or_load_evidence_bundle

  -> sql 可选
      如果 evidence 过期或用户要求最新数据，则补充查询

  -> knowledge 可选
      如果需要引用 SOP / 手册 / 标准模板，则查询知识库

  -> analysis
      整理结论
      整理证据
      整理时间线
      整理建议

  -> report
      按模板生成报告

  -> evidence_validation
      检查报告每个结论是否有证据
      检查是否遗漏限制条件
      检查是否混入未验证猜测

  -> final_answer
  -> output_guardrail
  -> save_artifact
  -> complete
```

### 报告模板

```text
1. 基本信息
2. 问题描述
3. 影响范围
4. 数据时间窗
5. 关键证据
6. 分析过程
7. 诊断结论
8. 置信度
9. 建议处理措施
10. 是否建议派单
11. 风险与限制
12. 附录：指标、告警、日志、知识库引用
```

---

# 9. 配置 / 变更影响分析：`config_change_impact`

### 适用问题

> “刚改了阈值，会不会导致误报？”
> “升级之后为什么告警变多了？”
> “参数调整和故障有关吗？”

### workflow

```text
start
  -> understand / route
  -> check_required_slots
      必需：变更对象 / 变更时间 / 设备范围
  -> select_workflow_policy: change_impact_policy
  -> initialize_evidence_bundle

  -> sql
      查询变更记录
      查询变更前后指标
      查询变更前后告警频次
      查询操作人 / 版本 / 参数值
      查询相关设备影响范围

  -> knowledge
      查询参数含义
      查询推荐范围
      查询版本说明
      查询已知问题

  -> analysis
      before-after 对比
      判断告警变化是否与配置相关
      判断是否可能误报
      判断是否需要回滚或调整

  -> workorder_decision
      是否建议变更回滚
      是否建议人工复核

  -> evidence_validation
      检查是否满足因果判断条件
  -> final_answer
  -> output_guardrail
  -> complete
```

---

# 10. 操作请求：`action_request`

### 适用问题

> “帮我重启设备。”
> “关闭这个告警。”
> “把阈值改成 90。”
> “创建工单。”

### 关键点

这是最高风险类型。建议初期只支持：

```text
生成建议
生成工单草稿
请求确认
```

不要直接执行控制动作。

### workflow

```text
start
  -> understand / route
  -> identify_action_type
  -> permission_check
  -> risk_check
  -> select_workflow_policy: action_request_policy
  -> initialize_evidence_bundle

  -> sql
      查询设备当前状态
      查询是否允许操作
      查询是否存在安全互锁
      查询当前告警和影响范围

  -> knowledge
      查询 SOP
      查询操作前置条件
      查询安全限制

  -> analysis
      判断操作是否合理
      判断风险等级
      判断是否需要审批

  -> action_decision
      deny / require_confirmation / create_draft / execute

  -> evidence_validation
      高风险操作必须有授权
      缺少安全条件时不能执行
  -> final_answer
  -> output_guardrail
  -> audit_log
  -> complete
```

### 建议规则

```text
低风险：查询、解释、生成报告
中风险：创建工单草稿、建议巡检
高风险：重启、停机、修改阈值、屏蔽告警
极高风险：绕过保护、关闭安全联锁、强制运行
```

高风险和极高风险必须走审批。

---

## 四、Task Policy Selector 怎么设计

`Task Policy Selector` 的作用是把分类结果转成执行约束。

建议每类任务配置一个 policy，而不是写死在代码里。

例如：

```json
{
  "policy_id": "fault_diagnosis_v1",
  "task_type": "fault_diagnosis",
  "workflow_id": "wf_fault_diagnosis_v1",
  "required_slots": [
    "device_or_scope",
    "symptom_or_alarm",
    "time_window"
  ],
  "default_values": {
    "time_window": "last_2_hours"
  },
  "allowed_tools": [
    "asset_db.read",
    "timeseries_db.read",
    "alarm_db.read",
    "event_log.read",
    "topology_db.read",
    "maintenance_db.read",
    "knowledge_base.search"
  ],
  "forbidden_tools": [
    "device_control.write",
    "config.write"
  ],
  "evidence_requirements": {
    "minimum_metric_points": 10,
    "need_alarm_context": true,
    "need_time_window": true,
    "need_knowledge_reference": true,
    "need_hypothesis_evidence_mapping": true
  },
  "llm_allowed_steps": [
    "query_normalization",
    "hypothesis_generation",
    "evidence_to_answer_synthesis"
  ],
  "llm_forbidden_steps": [
    "direct_tool_execution",
    "unsupported_root_cause_assertion"
  ],
  "output_schema": "fault_diagnosis_answer_v1",
  "on_missing_evidence": "ask_clarifying_question_or_return_insufficient_evidence",
  "guardrails": [
    "no_conclusion_without_evidence",
    "no_control_action",
    "show_uncertainty"
  ]
}
```

重点是：**policy 不是 prompt，而是执行约束。**

---

## 五、Evidence Store / Evidence Bundle 的核心设计

你这个架构里最关键的是 evidence bundle。它应该成为所有诊断结论的唯一来源。

建议 evidence item 统一结构：

```json
{
  "evidence_id": "ev_001",
  "source_type": "timeseries_db",
  "source_name": "temperature_sensor",
  "device_id": "pump_001",
  "time_range": {
    "start": "2026-06-16T09:00:00",
    "end": "2026-06-16T10:00:00"
  },
  "content": {
    "metric": "temperature",
    "max": 92.5,
    "avg": 88.1,
    "unit": "°C",
    "threshold": 85
  },
  "quality": {
    "freshness": "fresh",
    "completeness": "complete",
    "reliability": "high"
  },
  "supports": ["h_001"],
  "refutes": [],
  "notes": "温度持续超过高阈值"
}
```

诊断结论必须引用 evidence id：

```json
{
  "claim": "最可能原因是冷却系统异常",
  "claim_type": "suspected_cause",
  "confidence": 0.78,
  "supporting_evidence": ["ev_001", "ev_002", "ev_007"],
  "missing_evidence": ["cooling_flow_rate"],
  "can_be_stated_as_fact": false
}
```

这个设计可以极大降低幻觉。

---

## 六、Evidence Validation 应该检查什么

建议把 validation 做成规则，而不是靠 LLM 自觉。

### 通用检查

```text
1. 是否有设备 / 系统对象
2. 是否有时间窗
3. 数据是否新鲜
4. 指标是否有单位
5. 阈值是否有来源
6. 告警是否有发生时间
7. 结论是否绑定证据
8. 是否存在缺失关键证据
9. 是否把猜测说成确定事实
10. 是否越权建议操作
```

### 针对诊断任务的检查

```text
1. 每个 root cause / suspected cause 是否有 supporting evidence
2. 是否列出 alternative causes
3. 是否列出 contradicting evidence
4. 是否标明 confidence
5. 是否说明 missing evidence
6. 是否区分 symptom、direct cause、root cause
```

### 针对 RCA 的检查

```text
1. 是否有完整时间线
2. 是否识别 first abnormal event
3. 是否区分直接原因和根本原因
4. 是否有恢复动作
5. 是否有预防措施
6. 是否避免把相关性写成因果性
```

---

## 七、最终建议的整体 workflow 模板

你原来的流程可以微调成下面这样：

```text
start
  -> query_normalize
      输出 task_json

  -> route
      输出 task_type / sub_type / modifiers / confidence

  -> slot_check
      检查设备、时间窗、症状、告警码、输出目标

  -> select_workflow_policy
      根据 task_type + risk_level + modifiers 选择 policy

  -> initialize_evidence_bundle
      初始化任务上下文、证据清单、缺失项、约束

  -> execute_workflow
      -> collect_asset_context
      -> collect_sql_evidence       按 policy 执行
      -> collect_knowledge_evidence 按 policy 执行
      -> perform_analysis           按 policy 执行
      -> make_workorder_decision    按 policy 执行
      -> generate_report            按 policy 执行

  -> evidence_validation
      不通过则：
        - 补充查询
        - 降低结论置信度
        - 追问用户
        - 输出证据不足

  -> answer_synthesis
      只基于 evidence bundle 生成

  -> output_guardrail
      检查格式、权限、风险、越权、幻觉

  -> save_artifact
      保存 evidence bundle、诊断结论、报告、审计日志

  -> complete
```

我会把 `final_answer` 放在 `output_guardrail` 前面更准确：先生成答案，再检查答案。

所以建议顺序是：

```text
evidence_validation
  -> answer_synthesis
  -> output_guardrail
  -> save_artifact
  -> complete
```

---

## 八、推荐的分类决策逻辑

可以先用规则 + LLM 组合。

### 第一层：强规则优先

```text
包含“报告 / 生成报告 / 总结成文档” -> report_generation
包含“RCA / 根因分析 / 复盘 / 事故分析” -> root_cause_analysis
包含“要不要派单 / 怎么处理 / 维修建议” -> maintenance_advice
包含“什么意思 / 手册 / 步骤 / 怎么操作” -> knowledge_qa
包含“现在状态 / 当前 / 多少 / 是否在线” -> status_query
包含“告警码 / 报警什么意思” -> alarm_interpretation
包含“为什么 / 诊断 / 原因 / 异常 / 故障” -> fault_diagnosis
包含“趋势 / 健康 / 风险 / 劣化 / 预测” -> health_assessment
包含“重启 / 停机 / 修改 / 关闭 / 执行” -> action_request
```

### 第二层：LLM 处理模糊场景

比如：

> “设备 A 高温了，看一下。”

这个可能是状态查询，也可能是故障诊断。

可以让 router 输出：

```json
{
  "task_type": "fault_diagnosis",
  "route_confidence": 0.72,
  "reason": "用户描述异常现象，并要求查看，默认需要诊断。",
  "fallback_task_type": "status_query"
}
```

### 第三层：低置信度时走保守 workflow

如果 `route_confidence < 0.6`，不要直接诊断，可以先走轻量 workflow：

```text
status_query + alarm_interpretation
```

然后再决定是否升级到 `fault_diagnosis`。

---

## 九、不同任务类型的默认 policy 总表

| task_type              | SQL | 知识库 | 分析 |    工单决策 | 报告 | 是否允许写操作 |
| ---------------------- | --: | --: | -: | ------: | -: | ------: |
| `status_query`         |  必需 |  可选 | 轻量 |       否 |  否 |       否 |
| `alarm_interpretation` |  可选 |  必需 | 中等 |      可选 |  否 |       否 |
| `fault_diagnosis`      |  必需 |  必需 | 必需 | 可选/建议开启 | 可选 |       否 |
| `root_cause_analysis`  |  必需 |  必需 | 必需 |      可选 | 必需 |       否 |
| `health_assessment`    |  必需 |  可选 | 必需 |      可选 | 可选 |       否 |
| `maintenance_advice`   |  必需 |  必需 | 必需 |      必需 | 可选 |   只允许草稿 |
| `knowledge_qa`         |  可选 |  必需 | 轻量 |       否 |  否 |       否 |
| `report_generation`    |  可选 |  可选 | 中等 |      可选 | 必需 |       否 |
| `config_change_impact` |  必需 |  必需 | 必需 |      可选 | 可选 |       否 |
| `action_request`       |  必需 |  必需 | 必需 |      必需 | 可选 |    需要审批 |

---

## 十、建议你优先落地的 MVP 分类

如果现在还在初期，不建议一上来做 10 类。可以先做 6 类：

```text
1. status_query
2. alarm_interpretation
3. fault_diagnosis
4. maintenance_advice
5. knowledge_qa
6. report_generation
```

然后把 `root_cause_analysis` 作为 `fault_diagnosis` 的增强版，把 `health_assessment` 作为后续版本。

MVP 的核心闭环是：

```text
查状态
解释告警
诊断原因
给处理建议
生成报告
```

这五件事已经覆盖大部分故障诊断 agent 的真实使用场景。

---

## 十一、一个比较推荐的最终设计

可以把你的系统抽象成：

```text
单 Agent = 任务理解器 + workflow 调度器 + 证据约束回答器
```

不要让 agent 自己决定“下一步做什么”，而是让它决定：

```text
这个用户问题属于哪类任务？
缺什么槽位？
应该使用哪个 policy？
最终如何基于 evidence bundle 表达？
```

真正的执行顺序由 workflow engine 控制。

最终结构可以是：

```text
User Query
  ↓
Query Normalizer
  ↓
Intent Router
  ↓
Task JSON
  ↓
Policy Selector
  ↓
Workflow Executor
  ↓
Evidence Bundle
  ↓
Evidence Validator
  ↓
Answer Synthesizer
  ↓
Output Guardrail
  ↓
Artifact Store / Audit Log
```

最核心的设计原则是：

> **分类决定 workflow，policy 决定工具和证据要求，evidence bundle 决定答案边界，guardrail 决定能不能输出。**

这样做之后，即使你采用的是单 agent 架构，也不会变成“一个大模型自由推理到底”，而是变成**可控、可审计、可扩展的诊断系统**。
