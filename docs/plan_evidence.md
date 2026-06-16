## **证据链**

> EvidenceItem：单条证据
Claim：基于证据形成的判断 / 断言
EvidenceBundle：一次任务的完整证据包
> 

```
工具结果 / 手册片段 / 时序特征
        ↓
EvidenceItem
        ↓
Claim
        ↓
EvidenceBundle
        ↓
最终回答 / 报告 / 工单决策
```

EvidenceItem 负责“事实从哪来”；Claim 负责“我据此判断了什么”；EvidenceBundle 负责“本次任务有哪些事实、判断、缺口和结论”。

#### **1、EvidenceItem：单条证据**

> EvidenceItem = 一条可追溯、可引用、可校验的事实证据。
> 

推荐schema：

```json
{
  "evidence_id": "ev_001",
  "evidence_type": "metric_snapshot",
  "source_type": "sql",
  "source_name": "device_status_table",
  "asset_id": "P101",
  "asset_type": "pump",
  "timestamp": "2026-06-16T13:20:00",
  "time_range": {
    "start": "2026-06-16T13:15:00",
    "end": "2026-06-16T13:20:00"
  },
  "content": {
    "metric": "bearing_temperature",
    "value": 92.4,
    "unit": "°C",
    "threshold": 80,
    "status": "abnormal"
  },
  "summary": "P101 轴承温度为 92.4°C，超过阈值 80°C。",
  "quality": {
    "reliability": "high",
    "freshness": "current",
    "relevance": "high",
    "completeness": "complete"
  },
  "metadata": {
    "query_id": "sql_abc123",
    "table": "device_status",
    "column": "bearing_temperature"
  }
}
```

- `evidence_id` 唯一ID，供后续引用
- `evidence_type` 表示证据类型

```
device_status
alarm_event
metric_snapshot
timeseries_feature
manual_reference
fault_code_reference
derived_feature
tool_error
```

- `source_type` 证据来源

```
sql
knowledge_base
user
analysis
rule_engine
diagnosis_model
visualization_tool
external_system
```

- `content` 结构化事实本体，不同证据类型有不同结构

```
设备状态：
{
  "running_state": "running",
  "communication": "normal",
  "alarm_status": "active",
  "health_score": 62
}
报警事件：
{
  "alarm_code": "E103",
  "alarm_name": "轴承温度高",
  "level": "high",
  "status": "active",
  "start_time": "2026-06-16T12:42:00"
}
```

- `summary` 给LLM、报告、最终回答使用的短摘要

```
原始数据存储在 artifact / database / feature table
EvidenceItem 里存结构化摘要和关键特征
例如："summary": "过去2小时 P101 轴承温度从 74.2°C 上升到 92.4°C，并连续38分钟超过阈值。"
```

- `quality` 判断证据是否可靠

```
{
  "reliability": "high",
  "freshness": "current",
  "relevance": "high",
  "completeness": "complete"
}

可以做成枚举：
reliability: high / medium / low
freshness: current / recent / stale / unknown
relevance: high / medium / low
completeness: complete / partial / missing
```

- 设计原则

> 1、一条EvidenceItem只表达一个核心事实
例如：
> 
> 
> {
> "summary": "P101 温度高、振动高、手册说可能是轴承问题、建议检修。"
> }
> 应当拆成：
> ev_001：当前温度超过阈值
> ev_002：过去2小时温度持续上升
> ev_003：同期振动升高
> ev_004：手册中 E103 表示轴承温度过高
> ev_005：手册建议检查润滑和轴承
> 
> 2、原始数据和解释分开
> EvidenceItem是事实，不是诊断结论
> 例如："summary": "P101 轴承温度 92.4°C，超过阈值 80°C。”
> 而不是："summary": "P101 轴承温度过高，说明轴承已经损坏。”（这是诊断结论，应当放在Claim里
> 

#### 2、Claim：基于证据形成的判断

> Claim是Agent的“判断单元”
没有Claim的话， `analysis`  节点容易直接输出一段自然语言，后续难以校验

Claim=一个可被证据支持活反驳的判断
可以是：
观察判断：P101 当前存在温度异常
故障模式判断：P101 符合轴承温升异常模式
根因判断：最可能原因是润滑不足或轴承磨损
风险判断：该故障存在继续恶化风险
建议判断：建议现场检查润滑和轴承
工单判断：建议生成检修工单
> 
- 推荐schema：

```json
{
  "claim_id": "claim_001",
  "claim_type": "root_cause_candidate",
  "asset_id": "P101",
  "statement": "P101 本次温度高报警最可能与润滑不足或轴承磨损有关。",
  "confidence": {
    "level": "medium",
    "score": 0.68,
    "reason": "温度和振动均异常，且与手册故障模式匹配，但缺少润滑油位和现场检查结果。"
  },
  "supporting_evidence_ids": [
    "ev_001",
    "ev_002",
    "ev_003",
    "ev_004"
  ],
  "contradicting_evidence_ids": [],
  "missing_evidence": [
    "润滑油位",
    "轴承现场检查结果",
    "冷却系统状态"
  ],
  "reasoning_summary": "温度持续升高，同时振动升高，符合手册中轴承温升异常的特征；手册给出的可能原因包括润滑不足和轴承磨损。",
  "status": "candidate",
  "created_by": "analysis_node"
}
```

- Claim类型设计

```json
观察类Claim
{
  "claim_type": "symptom_observed",
  "statement": "P101 当前存在轴承温度过高现象。",
  "supporting_evidence_ids": ["ev_001", "ev_002"],
  "confidence": {
    "level": "high",
    "score": 0.95
  }
}

故障码解释类Claim
{
  "claim_type": "fault_code_interpretation",
  "statement": "报警码 E103 在手册中表示轴承温度过高。",
  "supporting_evidence_ids": ["ev_004"],
  "confidence": {
    "level": "high",
    "score": 0.98
  }
}

根因候选类Claim
{
  "claim_type": "root_cause_candidate",
  "statement": "润滑不足是本次温度升高的可能原因之一。",
  "supporting_evidence_ids": ["ev_001", "ev_002", "ev_004"],
  "contradicting_evidence_ids": [],
  "missing_evidence": ["润滑油位"],
  "confidence": {
    "level": "medium",
    "score": 0.62
  }
}

风险评估类Claim
{
  "claim_type": "risk_assessment",
  "statement": "如果温度继续维持高位，存在轴承进一步损坏和停机风险。",
  "supporting_evidence_ids": ["ev_001", "ev_002", "ev_004"],
  "confidence": {
    "level": "medium",
    "score": 0.7
  }
}

工单决策类Claim
{
  "claim_type": "workorder_decision",
  "statement": "建议生成检修工单，安排现场检查润滑和轴承状态。",
  "decision": "suggest_create",
  "supporting_evidence_ids": ["ev_001", "ev_002", "ev_004"],
  "reason_codes": [
    "temperature_above_threshold",
    "alarm_active",
    "manual_recommends_inspection"
  ],
  "requires_user_confirmation": true,
  "confidence": {
    "level": "medium",
    "score": 0.72
  }
}
```

- Claim关键规则
    
    1、Claim必须引用EvidenceItem
    2、缺少证据要显示记录（如上面根因候选类Claim的 `missing_evidence` 
    3、反证要记录（例如sql查询与手册说明不一致时需要记录并降低手册置信度
    4、Claim里只放简短推理摘要，不放长链路思考
    

#### 3、EvidenceBundle：一次任务的完整证据包

> EvidenceBundle是整个任务执行过程中的“事实账本”
可以放在agent state里，让每个节点来读
> 
> 
> understand 节点写入 TaskSpec
> sql 节点写入 SQL 证据
> knowledge 节点写入手册证据
> analysis 节点写入 Claims
> workorder_decision 节点写入工单 Claim
> report 节点读取 Bundle 生成报告
> final_answer 节点读取 Bundle 生成回答
> 
- 推荐schema

```json
{
  "bundle_id": "bundle_20260616_001",
  "trace_id": "langfuse_trace_xxx",
  "task": {
    "task_type": "fault_diagnosis",
    "workflow_id": "WF_FAULT_DIAGNOSIS_V1",
    "workflow_version": "1.0.0",
    "user_query": "P101 为什么温度高？",
    "asset_id": "P101",
    "asset_type": "pump",
    "symptom": "temperature_high",
    "time_range": {
      "start": "2026-06-16T11:20:00",
      "end": "2026-06-16T13:20:00"
    }
  },
  "evidence_items": [ //建议用map，方便按id查询
    {
      "evidence_id": "ev_001",
      "evidence_type": "metric_snapshot",
      "summary": "P101 轴承温度 92.4°C，超过阈值 80°C。"
    },
    {
      "evidence_id": "ev_002",
      "evidence_type": "timeseries_feature",
      "summary": "过去2小时轴承温度持续上升，并连续38分钟超过阈值。"
    }
  ],
  "claims": [
    {
      "claim_id": "claim_001",
      "claim_type": "root_cause_candidate",
      "statement": "P101 本次温度高报警最可能与润滑不足或轴承磨损有关。",
      "supporting_evidence_ids": ["ev_001", "ev_002", "ev_004"]
    }
  ],
  "final_claim_ids": ["claim_001"],
  "quality_checks": {
    "has_asset": true,
    "has_current_status": true,
    "has_alarm_history": true,
    "has_manual_reference": true,
    "has_timeseries_feature": true,
    "all_claims_have_evidence": true,
    "missing_evidence_disclosed": true
  },
  "artifacts": {
    "chart_ids": [],
    "report_id": null
  }
}
```

- EvidenceBundle流程变化

```json
# 以用户询问P101为什么温度高为例
Step1：understand/route后：
{
  "task": {
    "task_type": "fault_diagnosis",
    "workflow_id": "WF_FAULT_DIAGNOSIS_V1",
    "asset_id": "P101",
    "symptom": "temperature_high"
  },
  "evidence_items": {},
  "claims": {}
}

Step2：SQL后
{
  "evidence_items": {
    "ev_001": {
      "evidence_type": "metric_snapshot",
      "summary": "P101 当前轴承温度 92.4°C，超过阈值 80°C。"
    },
    "ev_002": {
      "evidence_type": "alarm_event",
      "summary": "P101 当前存在 E103 轴承温度高报警。"
    },
    "ev_003": {
      "evidence_type": "timeseries_feature",
      "summary": "过去2小时轴承温度持续上升。"
    }
  }
}

Step3：knowledge后：
{
  "evidence_items": {
    "ev_004": {
      "evidence_type": "manual_reference",
      "summary": "手册中 E103 表示轴承温度过高，可能原因包括润滑不足、轴承磨损、负载过高和冷却异常。"
    }
  }
}

Step4：analysis后：
{
  "claims": {
    "claim_001": {
      "claim_type": "root_cause_candidate",
      "statement": "润滑不足或轴承磨损是较可能原因。",
      "supporting_evidence_ids": ["ev_001", "ev_003", "ev_004"],
      "missing_evidence": ["润滑油位", "轴承现场检查结果"],
      "confidence": {
        "level": "medium",
        "score": 0.68
      }
    },
    "claim_002": {
      "claim_type": "root_cause_candidate",
      "statement": "负载过高可能性较低。",
      "supporting_evidence_ids": ["ev_004"],
      "contradicting_evidence_ids": ["ev_005"],
      "confidence": {
        "level": "low",
        "score": 0.24
      }
    }
  }
}

Step5：final_answer只读Buddle，生成回答：
P101 当前确实存在轴承温度高异常。当前温度为 92.4°C，超过阈值 80°C，且过去2小时温度持续上升。

结合报警 E103 和故障手册，较可能原因是润滑不足或轴承磨损，置信度中等。

依据：
1. P101 当前轴承温度超过阈值。
2. 过去2小时温度持续上升。
3. 手册中 E103 对应轴承温度过高，可能原因包括润滑不足、轴承磨损、负载过高、冷却异常。

需要进一步确认：
- 润滑油位
- 轴承现场状态
- 冷却系统状态
```