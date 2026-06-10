"""Workflow 分阶段 Prompt。"""

from .contracts import (
    AnalysisStepArtifact,
    ClarificationArtifact,
    DiagnosisRequest,
    InspectionStepArtifact,
    KnowledgeStepArtifact,
    ManualQaArtifact,
    PlanningArtifact,
    ReportStepArtifact,
    SqlStepArtifact,
)


def _format_planning_context(planning_artifact: PlanningArtifact | None) -> str:
    """把 planner 产物整理成适合注入 Prompt 的简短上下文。"""

    if planning_artifact is None:
        return "无"
    return (
        f"任务摘要：{planning_artifact.task_summary}\n"
        f"目标：{'; '.join(planning_artifact.diagnosis_goals) or '无'}\n"
        f"必需证据：{'; '.join(item.description for item in planning_artifact.required_evidence) or '无'}\n"
        f"约束：{'; '.join(item.description for item in planning_artifact.constraints) or '无'}\n"
        f"风险：{'; '.join(planning_artifact.risk_flags) or '无'}\n"
        f"成功标准：{'; '.join(planning_artifact.success_criteria) or '无'}"
    )


def build_understanding_prompt(user_message: str, user_identity: str) -> str:
    """构建请求理解 Prompt。"""

    return f"""
你是 DCMA 工作流的请求理解器。
请阅读用户问题，并输出一个 JSON 对象，字段必须完整：
- user_message
- user_identity
- equipment_hint
- metric_hint
- fault_code_hint
- time_range_hint
- needs_report
- report_format
- analysis_goal

要求：
1. 只输出 JSON，不要输出解释。
2. 如果某字段无法判断，填 null。
3. needs_report 固定为 true。
4. report_format 固定为 "markdown"。

用户身份：{user_identity}
用户问题：{user_message}
""".strip()


def build_sql_generation_prompt(request: DiagnosisRequest, schema_context: str = "") -> str:
    """构建 SQL 生成 Prompt。"""

    return f"""
你是 DCMA 数据查询规划器。
请基于用户目标生成一个 JSON 对象，字段如下：
- sql_query: 字符串，必须是单条可执行 SQL
- summary: 用一句话说明这条 SQL 在查什么

要求：
1. 只输出 JSON。
2. 只生成查询语句，不生成解释性文本。
3. 如果用户提到时间范围，请尽量体现在 SQL 中。
4. 如果用户没有明确字段名，也应尽量根据目标给出合理查询。

用户问题：{request.user_message}
分析目标：{request.analysis_goal}
设备提示：{request.equipment_hint}
指标提示：{request.metric_hint}
故障码提示：{request.fault_code_hint}
时间范围提示：{request.time_range_hint}
可用表结构补充：{schema_context or "无"}
""".strip()


def build_analysis_prompt(
    request: DiagnosisRequest,
    sql_artifact: SqlStepArtifact,
    knowledge_artifact: KnowledgeStepArtifact,
    current_time: str,
    planning_artifact: PlanningArtifact | None = None,
) -> str:
    """构建分析阶段 Prompt。"""

    planning_text = _format_planning_context(planning_artifact)
    return f"""
你是 DCMA 诊断分析器。
请仅基于输入材料输出 JSON，字段如下：
- conclusion: 一句话结论
- basis: 数组，列出支撑结论的关键事实
- recommendations: 数组，列出处置建议
- risk_notice: 字符串或 null
- missing_information: 数组，列出仍然缺失的信息
- confidence: 只能取 high / medium / low

要求：
1. 只输出 JSON。
2. 不允许编造未提供的数据。
3. 如果 SQL 数据不足或知识依据不足，confidence 不得为 high。
4. basis 必须尽量引用 SQL 结果或知识检索内容中的事实。
5. 每个具体结论都要能对应到 SQL 结果或知识检索事实；证据不足时要在 missing_information 中说明缺口。
6. 对根因、能否出报告、能否下结论这类判断，必须区分“已确认结论”和“待确认假设”。

当前时间：{current_time}
用户问题：{request.user_message}
分析目标：{request.analysis_goal}

执行计划与约束：
{planning_text}

SQL 摘要：{sql_artifact.summary}
SQL 结果预览：
{sql_artifact.result_preview or sql_artifact.raw_output or "无"}

知识检索结果：
{knowledge_artifact.raw_output or "无"}
""".strip()


def build_final_answer_prompt(
    analysis_artifact: AnalysisStepArtifact,
    report_artifact: ReportStepArtifact | None,
    planning_artifact: PlanningArtifact | None = None,
) -> str:
    """构建最终答复 Prompt。"""

    report_name = report_artifact.report_filename if report_artifact else None
    planning_text = _format_planning_context(planning_artifact)
    return f"""
你是 DCMA 诊断结果整理器。
请用中文生成最终用户答复，要求结构清晰，必须包含：
1. 一句话结论
2. 数据支撑
3. 处理建议
4. 风险提示（如果有）
5. 报告文件名

组织要求：
1. 优先按“结论 -> 对应证据 -> 不确定性 -> 下一步动作”表达。
2. 如果置信度不是 high，必须明确说明当前不能把根因或报告结论说成已确认。

结论：{analysis_artifact.conclusion}
依据：{analysis_artifact.basis}
建议：{analysis_artifact.recommendations}
风险提示：{analysis_artifact.risk_notice}
缺失信息：{analysis_artifact.missing_information}
置信度：{analysis_artifact.confidence}
报告文件名：{report_name or "未生成"}
执行计划与成功标准：
{planning_text}
""".strip()


def build_status_inspection_understanding_prompt(user_message: str, user_identity: str) -> str:
    """构建状态巡检请求理解 Prompt。"""

    return f"""
你是 DCMA 状态巡检流的请求理解器。
请阅读用户问题，并输出一个 JSON 对象，字段必须完整：
- user_message
- user_identity
- equipment_hint
- metric_hint
- fault_code_hint
- time_range_hint
- needs_report
- report_format
- analysis_goal

要求：
1. 只输出 JSON，不要输出解释。
2. 如果某字段无法判断，填 null。
3. 当用户明确要求“生成报告 / 输出报告 / 整理成报告”时，needs_report 才为 true，否则为 false。
4. report_format 固定为 "markdown"。
5. analysis_goal 要体现“运行状态、巡检摘要、风险判断、趋势观察”等巡检目标，而不是故障根因诊断。

用户身份：{user_identity}
用户问题：{user_message}
""".strip()


def build_status_inspection_sql_prompt(request: DiagnosisRequest, schema_context: str = "") -> str:
    """构建状态巡检 SQL 生成 Prompt。"""

    return f"""
你是 DCMA 状态巡检数据查询规划器。
请基于用户目标生成一个 JSON 对象，字段如下：
- sql_query: 字符串，必须是单条可执行 SQL
- summary: 用一句话说明这条 SQL 在查什么

要求：
1. 只输出 JSON。
2. SQL 目标应偏向运行概览、关键指标摘要、异常趋势、风险信号，而不是故障根因深挖。
3. 如果用户提到时间范围，请尽量体现在 SQL 中。
4. 若用户未给具体指标，也应围绕运行状态生成合理摘要查询。

用户问题：{request.user_message}
巡检目标：{request.analysis_goal}
设备提示：{request.equipment_hint}
指标提示：{request.metric_hint}
故障码提示：{request.fault_code_hint}
时间范围提示：{request.time_range_hint}
可用表结构补充：{schema_context or "无"}
""".strip()


def build_status_inspection_analysis_prompt(
    request: DiagnosisRequest,
    sql_artifact: SqlStepArtifact,
    knowledge_artifact: KnowledgeStepArtifact | None,
    current_time: str,
) -> str:
    """构建状态巡检分析 Prompt。"""

    knowledge_text = knowledge_artifact.raw_output if knowledge_artifact else "本次巡检未启用知识补充"
    return f"""
你是 DCMA 状态巡检分析器。
请仅基于输入材料输出 JSON，字段如下：
- summary: 一句话巡检摘要
- observed_metrics: 数组，列出本次巡检关注到的指标或现象
- detected_anomalies: 数组，列出识别到的异常或风险信号
- risk_level: 只能取 high / medium / low
- suggested_actions: 数组，列出建议动作
- confidence: 只能取 high / medium / low

要求：
1. 只输出 JSON。
2. 目标是判断“当前运行状态是否健康”，不是解释故障根因。
3. 不允许编造未提供的数据。
4. 如果 SQL 数据不足，confidence 不得为 high。
5. 若未发现明确异常，detected_anomalies 可为空数组，但 summary 必须说明当前状态判断。

当前时间：{current_time}
用户问题：{request.user_message}
巡检目标：{request.analysis_goal}

SQL 摘要：{sql_artifact.summary}
SQL 结果预览：
{sql_artifact.result_preview or sql_artifact.raw_output or "无"}

知识补充：
{knowledge_text}
""".strip()


def build_status_inspection_final_answer_prompt(
    inspection_artifact: InspectionStepArtifact,
    report_artifact: ReportStepArtifact | None,
) -> str:
    """构建状态巡检最终答复 Prompt。"""

    report_name = report_artifact.report_filename if report_artifact else None
    return f"""
你是 DCMA 状态巡检结果整理器。
请用中文生成最终用户答复，要求结构清晰，必须包含：
1. 巡检摘要
2. 观察到的指标或现象
3. 异常与风险提示
4. 建议动作
5. 报告文件名

巡检摘要：{inspection_artifact.summary}
观察指标：{inspection_artifact.observed_metrics}
发现异常：{inspection_artifact.detected_anomalies}
风险等级：{inspection_artifact.risk_level}
建议动作：{inspection_artifact.suggested_actions}
置信度：{inspection_artifact.confidence}
报告文件名：{report_name or "未生成"}
""".strip()


def build_manual_qa_understanding_prompt(user_message: str, user_identity: str) -> str:
    """构建手册问答请求理解 Prompt。"""

    return f"""
你是 DCMA 手册问答流的请求理解器。
请阅读用户问题，并输出一个 JSON 对象，字段必须完整：
- user_message
- user_identity
- equipment_hint
- metric_hint
- fault_code_hint
- time_range_hint
- needs_report
- report_format
- analysis_goal

要求：
1. 只输出 JSON，不要输出解释。
2. 如果某字段无法判断，填 null。
3. needs_report 固定为 false。
4. report_format 固定为 "markdown"。
5. analysis_goal 要体现“故障码释义、操作说明、安全注意事项、维修步骤、手册知识问答”等目标。

用户身份：{user_identity}
用户问题：{user_message}
""".strip()


def build_manual_qa_answer_prompt(
    request: DiagnosisRequest,
    knowledge_artifact: KnowledgeStepArtifact,
) -> str:
    """构建手册问答整理 Prompt。"""

    return f"""
你是 DCMA 手册问答整理器。
请仅基于知识检索结果输出 JSON，字段如下：
- question_type: 字符串，例如 故障码释义 / 操作说明 / 安全注意事项 / 维修步骤 / 其他问答
- answer: 字符串，面向用户的最终回答
- missing_information: 数组，列出当前知识仍不足的点
- confidence: 只能取 high / medium / low

要求：
1. 只输出 JSON。
2. 只能依据知识检索内容作答，不允许补充未提供的步骤和数据。
3. 回答中要尽量明确引用已检索到的知识依据。
4. 如果知识不足，必须在 answer 中明确说明当前知识不足，不得编造。
5. 当知识片段较少或表述不完整时，confidence 不得为 high。

用户问题：{request.user_message}
问答目标：{request.analysis_goal}
故障码提示：{request.fault_code_hint}
设备提示：{request.equipment_hint}

知识查询：{knowledge_artifact.query}
知识检索结果：
{knowledge_artifact.raw_output or "无"}
""".strip()


def build_manual_qa_final_answer_prompt(manual_qa_artifact: ManualQaArtifact) -> str:
    """构建手册问答最终答复 Prompt。"""

    return f"""
你是 DCMA 手册问答结果整理器。
请用中文生成最终用户答复，要求结构清晰，必须包含：
1. 问题类型
2. 最终回答
3. 知识依据摘要
4. 仍缺少的信息（如果有）

问题类型：{manual_qa_artifact.question_type}
最终回答：{manual_qa_artifact.answer}
知识片段：{manual_qa_artifact.snippets}
缺失信息：{manual_qa_artifact.missing_information}
置信度：{manual_qa_artifact.confidence}
""".strip()


def build_clarification_understanding_prompt(user_message: str, user_identity: str) -> str:
    """构建澄清流请求理解与信息补全 Prompt。"""

    return f"""
你是 DCMA 澄清流的请求理解器。
请阅读用户问题，并输出一个 JSON 对象，字段必须完整：
- user_message
- user_identity
- equipment_hint
- metric_hint
- fault_code_hint
- time_range_hint
- needs_report
- report_format
- analysis_goal
- candidate_workflows
- missing_slots
- clarifying_questions
- reason
- suggested_next_workflow
- confidence

要求：
1. 只输出 JSON，不要输出解释。
2. 如果某字段无法判断，填 null 或空数组。
3. needs_report 固定为 false。
4. report_format 固定为 "markdown"。
5. candidate_workflows 只允许从以下值中选择：fault_diagnosis / status_inspection / manual_qa / report_generation。
6. missing_slots 只允许使用：equipment_hint / metric_hint / fault_code_hint / time_range_hint。
7. clarifying_questions 只生成当前最小必要的补充问题，数量 1 到 3 条。
8. suggested_next_workflow 必须从 candidate_workflows 中选择，若无法判断则填 null。
9. confidence 只能取 high / medium / low。

用户身份：{user_identity}
用户问题：{user_message}
""".strip()


def build_clarification_final_answer_prompt(clarification_artifact: ClarificationArtifact) -> str:
    """构建澄清流最终答复 Prompt。"""

    return f"""
你是 DCMA 澄清流结果整理器。
请用中文生成最终用户答复，要求结构清晰、简洁，不要直接给出诊断结论，必须包含：
1. 为什么当前需要先补充信息
2. 当前最可能的候选场景流
3. 需要补充的关键信息
4. 建议用户直接回答的澄清问题

候选场景流：{clarification_artifact.candidate_workflows}
缺失槽位：{clarification_artifact.missing_slots}
澄清问题：{clarification_artifact.clarifying_questions}
原因：{clarification_artifact.reason}
建议下一步场景：{clarification_artifact.suggested_next_workflow}
置信度：{clarification_artifact.confidence}
""".strip()
