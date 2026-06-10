"""planner 子 Agent Prompt 模板。"""

from __future__ import annotations

from typing import Any

from ..contracts import PlanningArtifact, WorkflowRouteResult


def _dump_route_result(route_result: WorkflowRouteResult | dict[str, Any] | None) -> dict[str, Any] | None:
    if route_result is None:
        return None
    if isinstance(route_result, dict):
        return dict(route_result)
    return route_result.model_dump()


PLANNER_JSON_PROMPT_HEADER = """
你是 DCMA Workflow 的任务治理型 planner。
请基于路由结果和规则计划补充任务摘要、诊断目标、风险点和成功标准。
只输出 JSON，不要输出 Markdown 或解释，不要调用 SQL、知识库、报告或任何业务工具。
""".strip()


def build_planner_json_prompt(
    *,
    message: str,
    user_identity: str,
    route_result: WorkflowRouteResult | dict[str, Any] | None,
    default_plan: PlanningArtifact,
) -> str:
    """构建 planner LLM JSON 增强 Prompt。"""

    return f"""
{PLANNER_JSON_PROMPT_HEADER}

输出必须是一个完整 JSON 对象，字段必须与 PlanningArtifact 完全一致：
- success: boolean
- task_summary: string
- workflow_type: string，只能保留路由给出的 workflow_type
- diagnosis_goals: string[]
- required_evidence: PlanningEvidenceRequirement[]
- constraints: PlanningConstraint[]
- risk_flags: string[]
- clarification_questions: string[]
- success_criteria: string[]
- confidence: high / medium / low
- fallback_used: false
- error: null

硬性边界：
1. 你只能增强任务摘要、目标、风险和成功标准，不得改变路由场景。
2. 不得删除规则计划中的必需证据和 blocking 约束。
3. 故障诊断必须保留必需 SQL 证据。
4. 手册问答不得强制要求 SQL 证据。
5. 报告生成必须依赖上游 artifact。
6. 澄清场景必须保留澄清问题。
7. 证据复核必须依赖上游 artifact 或 evidence bundle。

用户身份：{user_identity}
用户请求：{message}

路由结果：
{_dump_route_result(route_result)}

规则计划：
{default_plan.model_dump()}
""".strip()


def build_planner_json_repair_prompt(raw_text: str, error_message: str) -> str:
    """构建 planner JSON 修复 Prompt。"""

    return f"""
你是 JSON 修复器。
下面内容原本应该是一个 PlanningArtifact JSON 对象，但解析失败。
请只输出修复后的 JSON 对象，不要输出解释、Markdown 或代码块。

解析错误：{error_message}

待修复内容：
{raw_text[:6000]}
""".strip()
