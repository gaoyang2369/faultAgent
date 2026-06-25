"""Registered final-answer templates for routed single-agent task types."""

from __future__ import annotations

from ..workflow.contracts import TaskType
from .contracts import OutputContract, OutputSectionContract


STATUS_QUERY_CONTRACT = OutputContract(
    task_type=TaskType.STATUS_QUERY,
    template_id="status_query_v1",
    description="用于设备当前状态、最近运行情况、是否在线等轻量查询。",
    tone="brief",
    require_evidence_ids=True,
    allow_workorder_suggestion=False,
    allow_report_link=False,
    missing_evidence_policy="answer_with_known_limits",
    max_bullets_per_section=4,
    max_chars=1200,
    sections=[
        OutputSectionContract(
            key="current_status",
            title="当前状态",
            require_evidence=True,
            fallback_when_missing="暂未查询到可用的实时状态数据。",
        ),
        OutputSectionContract(
            key="key_metrics",
            title="关键指标",
            required=False,
            require_evidence=True,
            fallback_when_missing="本次未获得足够的关键指标数据。",
        ),
        OutputSectionContract(
            key="brief_judgement",
            title="简要判断",
            require_evidence=True,
            fallback_when_missing="由于运行数据不足，暂不能判断设备是否异常。",
        ),
        OutputSectionContract(key="data_boundary", title="数据依据与边界"),
    ],
)

ALARM_TRIAGE_CONTRACT = OutputContract(
    task_type=TaskType.ALARM_TRIAGE,
    template_id="alarm_triage_v1",
    description="用于故障码、告警码解释、当前状态确认和严重程度分诊。",
    tone="diagnostic",
    require_evidence_ids=True,
    allow_workorder_suggestion=True,
    allow_report_link=False,
    missing_evidence_policy="disclose_and_downgrade",
    max_bullets_per_section=5,
    max_chars=1800,
    sections=[
        OutputSectionContract(
            key="alarm_explanation",
            title="告警解释",
            require_evidence=True,
            fallback_when_missing="知识库中暂未检索到该告警码的明确解释。",
        ),
        OutputSectionContract(
            key="current_alarm_status",
            title="当前告警状态",
            require_evidence=True,
            fallback_when_missing="暂未查询到该告警的实时状态，不能确认当前是否仍在发生。",
        ),
        OutputSectionContract(
            key="severity_assessment",
            title="严重程度判断",
            require_evidence=True,
            fallback_when_missing="由于缺少实时告警状态，暂不能给出确定严重程度。",
        ),
        OutputSectionContract(key="recommended_actions", title="处置建议", require_evidence=True),
        OutputSectionContract(key="missing_evidence", title="证据不足说明"),
    ],
)

FAULT_DIAGNOSIS_CONTRACT = OutputContract(
    task_type=TaskType.FAULT_DIAGNOSIS,
    template_id="fault_diagnosis_v1",
    description="用于设备异常、故障原因判断、处置建议和工单建议。",
    tone="diagnostic",
    require_evidence_ids=True,
    allow_workorder_suggestion=True,
    allow_report_link=True,
    missing_evidence_policy="disclose_and_downgrade",
    max_bullets_per_section=5,
    max_chars=2600,
    sections=[
        OutputSectionContract(
            key="diagnosis_conclusion",
            title="诊断结论",
            require_evidence=True,
            fallback_when_missing="目前证据不足，不能给出确定诊断结论，只能列出候选原因。",
        ),
        OutputSectionContract(
            key="current_status",
            title="当前状态",
            require_evidence=True,
            fallback_when_missing="暂未获得足够的当前运行状态数据。",
        ),
        OutputSectionContract(
            key="key_evidence",
            title="关键证据",
            require_evidence=True,
            fallback_when_missing="本次诊断缺少可用证据，不能支持明确结论。",
        ),
        OutputSectionContract(
            key="possible_causes",
            title="可能原因",
            require_evidence=True,
            fallback_when_missing="由于证据不足，暂不能形成可靠的原因排序。",
        ),
        OutputSectionContract(key="recommendations", title="处置建议", require_evidence=True),
        OutputSectionContract(
            key="workorder_decision",
            title="工单建议",
            required=False,
            require_evidence=True,
            fallback_when_missing="当前证据不足，暂不建议自动生成工单，可先补充运行数据和告警记录。",
        ),
        OutputSectionContract(key="limitations", title="证据不足说明"),
    ],
)

ROOT_CAUSE_ANALYSIS_CONTRACT = OutputContract(
    task_type=TaskType.ROOT_CAUSE_ANALYSIS,
    template_id="root_cause_analysis_v1",
    description="用于故障复盘、根因分析和影响评估。",
    tone="formal_report",
    require_evidence_ids=True,
    allow_workorder_suggestion=True,
    allow_report_link=True,
    missing_evidence_policy="disclose_and_downgrade",
    max_bullets_per_section=6,
    max_chars=3200,
    sections=[
        OutputSectionContract(key="event_summary", title="事件摘要", require_evidence=True),
        OutputSectionContract(key="timeline", title="时间线", require_evidence=True),
        OutputSectionContract(key="root_cause_candidates", title="根因候选", require_evidence=True),
        OutputSectionContract(key="excluded_causes", title="已排除或暂不支持的原因", required=False, require_evidence=True),
        OutputSectionContract(key="impact_assessment", title="影响评估", require_evidence=True),
        OutputSectionContract(key="prevention_recommendations", title="复发预防建议", require_evidence=True),
        OutputSectionContract(key="limitations", title="证据不足说明"),
    ],
)

HEALTH_ASSESSMENT_CONTRACT = OutputContract(
    task_type=TaskType.HEALTH_ASSESSMENT,
    template_id="health_assessment_v1",
    description="用于设备健康评分、趋势评估和风险预警。",
    tone="diagnostic",
    require_evidence_ids=True,
    allow_workorder_suggestion=False,
    allow_report_link=True,
    missing_evidence_policy="answer_with_known_limits",
    max_bullets_per_section=5,
    max_chars=2400,
    sections=[
        OutputSectionContract(key="health_score", title="健康等级/评分", require_evidence=True),
        OutputSectionContract(key="trend_analysis", title="趋势变化", require_evidence=True),
        OutputSectionContract(key="risk_items", title="主要风险项", require_evidence=True),
        OutputSectionContract(key="watch_metrics", title="建议关注指标", require_evidence=True),
        OutputSectionContract(key="maintenance_advice", title="维护建议", require_evidence=True),
        OutputSectionContract(key="prediction_boundary", title="预测边界说明"),
    ],
)

KNOWLEDGE_QA_CONTRACT = OutputContract(
    task_type=TaskType.KNOWLEDGE_QA,
    template_id="knowledge_qa_v1",
    description="用于故障码、SOP、手册、维护规范等知识库问答。",
    tone="brief",
    require_evidence_ids=True,
    allow_workorder_suggestion=False,
    allow_report_link=False,
    missing_evidence_policy="answer_with_known_limits",
    max_bullets_per_section=4,
    max_chars=1500,
    sections=[
        OutputSectionContract(key="answer", title="回答", require_evidence=True),
        OutputSectionContract(key="scope", title="适用范围", required=False, require_evidence=True),
        OutputSectionContract(key="sources", title="依据来源", require_evidence=True),
        OutputSectionContract(key="safety_note", title="安全提示"),
    ],
)

REPORT_GENERATION_CONTRACT = OutputContract(
    task_type=TaskType.REPORT_GENERATION,
    template_id="report_generation_v1",
    description="用于生成正式运行报告、诊断报告或 RCA 报告。",
    tone="formal_report",
    require_evidence_ids=True,
    allow_workorder_suggestion=True,
    allow_report_link=True,
    missing_evidence_policy="disclose_and_downgrade",
    max_bullets_per_section=8,
    max_chars=None,
    sections=[
        OutputSectionContract(key="report_status", title="报告状态", require_evidence=False),
        OutputSectionContract(key="report_title", title="报告标题", require_evidence=False),
        OutputSectionContract(key="report_summary", title="报告摘要", require_evidence=True),
        OutputSectionContract(key="report_link", title="报告链接", require_evidence=False),
        OutputSectionContract(key="missing_evidence_notice", title="证据不足提示"),
    ],
)

ACTION_REQUEST_CONTRACT = OutputContract(
    task_type=TaskType.ACTION_REQUEST,
    template_id="action_request_v1",
    description="用于重启、停机、关闭告警、派发工单、修改参数等动作类请求。",
    tone="safety_boundary",
    require_evidence_ids=False,
    allow_workorder_suggestion=True,
    allow_report_link=False,
    missing_evidence_policy="ask_for_more_info",
    max_bullets_per_section=5,
    max_chars=1600,
    sections=[
        OutputSectionContract(key="cannot_execute", title="无法直接执行", require_evidence=False),
        OutputSectionContract(key="available_help", title="可提供的帮助", require_evidence=False),
        OutputSectionContract(key="required_confirmation", title="执行前需要确认", require_evidence=False),
        OutputSectionContract(key="next_step", title="建议下一步", require_evidence=False),
    ],
)

PERMISSION_SCOPE_QUERY_CONTRACT = OutputContract(
    task_type=TaskType.PERMISSION_SCOPE_QUERY,
    template_id="permission_scope_query_v1",
    description="用于说明当前身份可访问的设备、数据窗口和不可用能力。",
    tone="brief",
    require_evidence_ids=False,
    allow_workorder_suggestion=False,
    allow_report_link=False,
    missing_evidence_policy="answer_with_known_limits",
    max_bullets_per_section=5,
    max_chars=1200,
    sections=[
        OutputSectionContract(key="identity_scope", title="当前身份", require_evidence=False),
        OutputSectionContract(key="accessible_assets", title="可访问设备", require_evidence=False),
        OutputSectionContract(key="available_capabilities", title="可用能力", require_evidence=False),
        OutputSectionContract(key="unavailable_capabilities", title="不可用能力", require_evidence=False),
    ],
)

OUTPUT_CONTRACTS: dict[TaskType, OutputContract] = {
    TaskType.STATUS_QUERY: STATUS_QUERY_CONTRACT,
    TaskType.ALARM_TRIAGE: ALARM_TRIAGE_CONTRACT,
    TaskType.FAULT_DIAGNOSIS: FAULT_DIAGNOSIS_CONTRACT,
    TaskType.ROOT_CAUSE_ANALYSIS: ROOT_CAUSE_ANALYSIS_CONTRACT,
    TaskType.HEALTH_ASSESSMENT: HEALTH_ASSESSMENT_CONTRACT,
    TaskType.KNOWLEDGE_QA: KNOWLEDGE_QA_CONTRACT,
    TaskType.REPORT_GENERATION: REPORT_GENERATION_CONTRACT,
    TaskType.ACTION_REQUEST: ACTION_REQUEST_CONTRACT,
    TaskType.PERMISSION_SCOPE_QUERY: PERMISSION_SCOPE_QUERY_CONTRACT,
}


def coerce_task_type(value: object) -> TaskType:
    """Return a known task type without leaking routing string quirks."""

    if isinstance(value, TaskType):
        return value
    try:
        return TaskType(str(value or ""))
    except ValueError:
        return TaskType.FAULT_DIAGNOSIS


def get_output_contract(task_type: TaskType | str | None) -> OutputContract:
    """Return the registered output contract for a task type."""

    return OUTPUT_CONTRACTS.get(coerce_task_type(task_type), FAULT_DIAGNOSIS_CONTRACT)
