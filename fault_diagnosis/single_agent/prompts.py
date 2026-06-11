"""Prompts for the restricted single-agent runtime."""

from __future__ import annotations

from ..diagnosis.contracts import DiagnosisRequest


def build_single_agent_understanding_prompt(user_message: str, user_identity: str) -> str:
    """Build the bounded request-understanding prompt."""

    return f"""
你是 DCMA 故障诊断系统的单 Agent 请求理解器。
请阅读用户问题，只输出一个 JSON 对象，字段必须完整：
- user_message
- user_identity
- equipment_hint
- metric_hint
- fault_code_hint
- time_range_hint
- analysis_goal
- needs_sql
- needs_knowledge
- needs_report
- report_format

字段含义：
1. needs_sql：只有需要查看设备实时/历史数据、告警记录、指标趋势或故障记录时才为 true。
2. needs_knowledge：需要查故障码含义、维修步骤、手册知识、原因解释或处置建议时为 true。
3. needs_report：只有用户明确要求“报告 / 出报告 / 生成报告 / 导出报告”时为 true。
4. report_format 固定为 "markdown"。

要求：
1. 只输出 JSON，不要输出解释、Markdown 或代码块。
2. 无法判断的槽位填 null。
3. 如果请求太泛，也要给出最小可执行的 analysis_goal，并将缺失信息留给后续分析阶段说明。
4. 用户说“dcma / DCMA 系统”时表示系统范围，不要把 dcma 填成具体 equipment_hint。

用户身份：{user_identity}
用户问题：{user_message}
""".strip()


def build_single_agent_analysis_prompt(
    request: DiagnosisRequest,
    sql_summary: str,
    sql_result: str,
    knowledge_result: str,
    current_time: str,
) -> str:
    """Build the single-agent diagnosis analysis prompt."""

    return f"""
你是 DCMA 限制型单 Agent 的诊断分析器。
请仅基于输入材料输出 JSON，字段如下：
- conclusion: 一句话结论
- basis: 数组，列出支撑结论的关键事实
- recommendations: 数组，列出处置建议
- risk_notice: 字符串或 null
- missing_information: 数组，列出仍然缺失的信息
- confidence: 只能取 high / medium / low

要求：
1. 只输出 JSON。
2. 不允许编造未提供的数据、故障码释义或维修步骤。
3. 如果 SQL 未执行或结果不足，不能把实时状态说成已确认。
4. 如果知识库未命中，不能把手册依据说成已确认。
5. 对“是否能直接出报告 / 是否能下结论”的问题，必须区分“已确认结论”和“待确认假设”。
6. 如果 SQL 结果中已经给出行数据，不要表述为“SQL 未执行或无结果”。
7. 当前处于功能演示模式，不要因为采集时间早于当前时间而输出“无法确认当前实时状态”。

当前时间：{current_time}
用户问题：{request.user_message}
分析目标：{request.analysis_goal}
设备提示：{request.equipment_hint}
故障码提示：{request.fault_code_hint}
指标提示：{request.metric_hint}
时间范围提示：{request.time_range_hint}

SQL 摘要：{sql_summary}
SQL 结果：
{sql_result or "未执行或无结果"}

知识库结果：
{knowledge_result or "未执行或无结果"}
""".strip()


def build_single_agent_evidence_synthesis_prompt(
    request: DiagnosisRequest,
    evidence_summary: str,
    fallback_conclusion: str,
    fallback_basis: list[str],
    fallback_recommendations: list[str],
    current_time: str,
) -> str:
    """Build a constrained LLM synthesis prompt over structured evidence."""

    return f"""
你是 DCMA 故障诊断专家，请基于“结构化证据摘要”进行诊断合成。
请只输出 JSON，字段必须完整：
- conclusion: 一句话结论，必须同时体现数据侧异常和 RAG/手册侧证据是否支持
- basis: 数组，列出 4-8 条支撑结论的关键证据；必须覆盖 SQL 数据特征和 RAG 知识
- recommendations: 数组，给出可执行建议；必须包含“立即处置”、“验证步骤”、“根因排查”三类动作，不要只说“查询手册”
- risk_notice: 字符串或 null
- missing_information: 数组，列出影响置信度的缺失信息
- confidence: 只能取 high / medium / low

约束：
1. 只基于输入证据，不能编造手册没有给出的故障码含义、原因或处理步骤。
2. 如果 RAG 已给出原因/处理步骤，必须把它转成现场可执行动作。
3. 如果 RAG 未命中，不能让用户自行查询作为主要建议；应先给出数据侧可验证动作，并把知识库缺口列入 missing_information。
4. 建议要具体到检查对象，例如状态字/控制字、复位前后异常码、母线电压、速度给定与反馈、负载率、温度、散热、运行使能、反馈链路。
5. 不要因为采集时间早于当前时间而否定演示数据，直接按给定样本做诊断。

当前时间：{current_time}
用户问题：{request.user_message}
分析目标：{request.analysis_goal}

结构化证据摘要：
{evidence_summary}

规则诊断兜底结论：{fallback_conclusion}
规则诊断兜底依据：{fallback_basis}
规则诊断兜底建议：{fallback_recommendations}
""".strip()
