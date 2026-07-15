"""Small deterministic safety and signal catalog for canonical turns.

The catalog intentionally does *not* classify ordinary business intent from
Chinese synonym inventories.  Primary semantic classification belongs to the
single LLM proposal and its canonicalizer.  These rules retain only facts that
can be verified locally, turn structure, explicit negation, and high-risk
action signals needed to keep the fallback safe.
"""

RULE_CATALOG = (
    {
        "rule_id": "entity.fault_code.shape.v1", "category": "fault_code_shape",
        "pattern": r"(?<![A-Za-z0-9])([A-Za-z]{1,3}\d{4,6})(?![A-Za-z0-9])",
        "semantic_value": "fault_code", "priority": 700, "confidence": 1.0,
        "allowed_clause_roles": ("slot",), "capture_group": 1, "normalizer": "uppercase",
        "example": "A07089",
    },
    {
        "rule_id": "entity.device.motor_reference.v1", "category": "device_pattern",
        "pattern": r"(?<![A-Za-z0-9])(?:(?:[A-Za-z]\d{2,4})?\s*电机\s*\d+|J\d+(?:号机)?)(?!\d)",
        "semantic_value": "device_reference", "priority": 690, "confidence": 1.0,
        "allowed_clause_roles": ("slot",), "flags": "IGNORECASE", "normalizer": "remove_spaces",
        "example": "G120电机2",
    },
    {
        "rule_id": "entity.time.relative_window.v1", "category": "time_pattern",
        "pattern": r"(?:最近|过去|近)\s*(?:\d+|[一二两三四五六七八九十半]+)\s*(?:分钟|小时|天|周)|(?:今天|当前|现在|此刻)",
        "semantic_value": "time_window", "priority": 680, "confidence": 1.0,
        "allowed_clause_roles": ("slot",), "attributes": {"relative": True},
        "example": "最近一小时",
    },
    {
        "rule_id": "entity.artifact.explicit_reference.v1", "category": "artifact_reference",
        "pattern": r"(?<![A-Za-z0-9])(?:artifact|analysis|report|diagnosis):[A-Za-z0-9._:-]+",
        "semantic_value": "artifact_reference", "priority": 670, "confidence": 1.0,
        "allowed_clause_roles": ("source",), "flags": "IGNORECASE", "attributes": {"explicit": True},
        "example": "analysis:case-7",
    },
    {
        "rule_id": "source.prior_result.marker.v1", "category": "source_marker",
        "pattern": r"(?:刚才|上一轮|上一次|前面|之前|基于|根据|使用|用).{0,12}(?:诊断|分析|报告|数据|结果)",
        "semantic_value": "source_reference", "priority": 660, "confidence": 1.0,
        "allowed_clause_roles": ("source",), "attributes": {"historical": True},
        "example": "刚才的诊断结果",
    },
    {
        "rule_id": "reference.correction.marker.v1", "category": "correction_marker",
        "pattern": r"(?:不是.+?是|改成|改查|更正为|应该是)",
        "semantic_value": "correction_reference", "priority": 650, "confidence": 1.0,
        "allowed_clause_roles": ("reference",),
        "example": "改成",
    },
    {
        "rule_id": "reference.deictic.marker.v1", "category": "deictic_marker",
        "pattern": r"(?:它|这个|那个|该设备|刚才|上一轮|上一次|前面|之前)",
        "semantic_value": "deictic_reference", "priority": 640, "confidence": 1.0,
        "allowed_clause_roles": ("reference",),
        "example": "它",
    },
    {
        "rule_id": "clause.boundary.punctuation.v1", "category": "clause_boundary",
        "pattern": r"[，,。；;！？!?]+|(?=(?:并且|然后|顺便|另外|再|最后|接着|并(?=给|提|生成|创建|诊断|查询|比较|解释)))",
        "semantic_value": "boundary", "priority": 600, "confidence": 1.0,
        "allowed_clause_roles": ("boundary",),
        "example": "，",
    },
    {
        "rule_id": "clause.linker.leading.v1", "category": "linker",
        "pattern": r"^(并且|并|然后|同时|顺便|再|另外|以及)\s*",
        "semantic_value": "leading_linker", "priority": 590, "confidence": 1.0,
        "allowed_clause_roles": ("linker",), "capture_group": 1,
        "example": "然后",
    },
    {
        "rule_id": "validation.model.forbidden_execution_content.v1", "category": "model_validation",
        "pattern": r"(?:\bsql\b|\bnode\b|\btool\b|\bpermission\b|\bauthori[sz](?:e|ation|ed)\b|权限|授权|工具名?|节点名?)",
        "semantic_value": "forbidden_execution_or_authorization_content", "priority": 1000, "confidence": 1.0,
        "allowed_clause_roles": ("validation",), "flags": "IGNORECASE",
        "example": "SQL",
    },
    # High-risk execution signals must still be recognized before an LLM
    # proposal can be considered.  The remaining entries are intentionally
    # narrow fallback anchors, not wording inventories.
    {
        "rule_id": "action.workorder.dispatch.v1", "category": "action_predicate",
        "pattern": r"(?:派发|派单|下发)(?:工单)?|安排.{0,6}(?:工程师|人员).{0,6}(?:处理|维修|处置)",
        "semantic_value": "dispatch_workorder", "priority": 930, "confidence": 1.0,
        "allowed_clause_roles": ("action",), "entity_ref_kinds": ("*",),
        "example": "派发工单",
    },
    {
        "rule_id": "action.workorder.create_draft.v1", "category": "action_predicate",
        "pattern": r"(?:创建|生成|新建).{0,8}(?:工单|维修单)(?:草稿)?",
        "semantic_value": "create_workorder_draft", "priority": 920, "confidence": 1.0,
        "allowed_clause_roles": ("action",), "entity_ref_kinds": ("*",),
        "example": "生成工单草稿",
    },
    {
        "rule_id": "action.workorder.evaluate.v1", "category": "action_predicate",
        "pattern": r"(?:工单|维修单).{0,8}(?:必要性|要不要|是否需要)|(?:是否需要|要不要).{0,8}(?:生成)?(?:工单|维修单)|(?:是否建议).{0,8}(?:报修|工单)|(?:需要生成).{0,8}(?:工单|维修单)",
        "semantic_value": "evaluate_workorder_need", "priority": 925, "confidence": 1.0,
        "allowed_clause_roles": ("action",), "entity_ref_kinds": ("*",),
        "example": "是否需要工单",
    },
    {
        "rule_id": "action.report.generate.v1", "category": "action_predicate",
        "pattern": r"(?:生成|导出|整理成).{0,8}(?:运行)?报告",
        "semantic_value": "generate_report", "priority": 800, "confidence": 1.0,
        "allowed_clause_roles": ("action",), "entity_ref_kinds": ("*",),
        "example": "生成运行报告",
    },
    {
        "rule_id": "action.fault_code.explain.v1", "category": "action_predicate",
        "pattern": r"(?:是什么|什么意思|解释|含义|故障码)",
        "semantic_value": "explain_fault_code", "priority": 790, "confidence": 1.0,
        "allowed_clause_roles": ("action",), "requires_entity_kind": "fault_code", "entity_ref_kinds": ("fault_code",),
        "example": "A07089是什么意思",
    },
    {
        "rule_id": "action.fault_code.explain_code_only.v1", "category": "action_predicate",
        "pattern": r"(?:查询|查)?[A-Za-z]{1,3}\d{4,6}",
        "semantic_value": "explain_fault_code", "priority": 785, "confidence": 1.0,
        "allowed_clause_roles": ("action",), "requires_entity_kind": "fault_code", "entity_ref_kinds": ("fault_code",),
        "match_mode": "fullmatch", "flags": "IGNORECASE", "inferred": True,
        "example": "查询A07089",
    },
    {
        "rule_id": "action.runtime.status.v1", "category": "action_predicate",
        "pattern": r"(?:查询|查看)(?:.{0,12}(?:状态|运行|异常))?|查.{0,12}(?:状态|运行|异常)|(?:当前|现在).{0,12}(?:状态|运行)|(?:改查|改成).{0,12}",
        "semantic_value": "check_runtime_status", "priority": 780, "confidence": 1.0,
        "allowed_clause_roles": ("action",), "entity_ref_kinds": ("device_reference",),
        "example": "查询G120电机1状态",
    },
    {
        "rule_id": "action.recommendation.explicit.v1", "category": "action_predicate",
        "pattern": r"(?:处理|处置)建议",
        "semantic_value": "resolution_recommendation", "priority": 775, "confidence": 1.0,
        "allowed_clause_roles": ("action",), "entity_ref_kinds": ("device_reference",),
        "example": "处理建议",
    },
    {
        "rule_id": "action.runtime.compare.v1", "category": "action_predicate",
        "pattern": r"(?:比较|对比)",
        "semantic_value": "compare_runtime_status", "priority": 805, "confidence": 1.0,
        "allowed_clause_roles": ("action",), "entity_ref_kinds": ("device_reference",),
        "example": "比较",
    },
    {
        "rule_id": "action.diagnosis.explicit.v1", "category": "action_predicate",
        "pattern": r"(?:故障)?诊断(?!结果)(?:故障|异常)?|(?:判断|是否存在).{0,10}(?:故障|异常)|(?:排查|分析).{0,24}(?:根因|故障|异常)|(?:它|这个).{0,8}(?:故障|异常)",
        "semantic_value": "diagnose_fault", "priority": 795, "confidence": 1.0,
        "allowed_clause_roles": ("action",), "entity_ref_kinds": ("device_reference",),
        "example": "故障诊断",
    },
)

CAPABILITY_LEXICON = {
    "check_runtime_status": ("action.runtime.status.v1",),
    "create_workorder_draft": ("action.workorder.create_draft.v1",),
    "diagnose_fault": ("action.diagnosis.explicit.v1",),
    "dispatch_workorder": ("action.workorder.dispatch.v1",),
    "evaluate_workorder_need": ("action.workorder.evaluate.v1",),
    "explain_fault_code": ("action.fault_code.explain.v1",),
    "generate_report": ("action.report.generate.v1",),
}
