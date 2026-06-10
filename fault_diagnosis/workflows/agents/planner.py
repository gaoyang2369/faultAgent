"""任务治理型 planner 子 Agent。"""

from __future__ import annotations

import ast
import inspect
import json
import os
import re
from collections.abc import Callable
from typing import Any

from ..contracts import (
    PlanningArtifact,
    PlanningConstraint,
    PlanningEvidenceRequirement,
    WorkflowRouteResult,
    WorkflowType,
)
from .prompts import build_planner_json_prompt, build_planner_json_repair_prompt

_planner_model = None
_JSON_BLOCK_RE = re.compile(r"```json\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_WORKFLOW_LABELS = {
    WorkflowType.FAULT_DIAGNOSIS.value: "故障诊断",
    WorkflowType.STATUS_INSPECTION.value: "状态巡检",
    WorkflowType.MANUAL_QA.value: "手册问答",
    WorkflowType.REPORT_GENERATION.value: "报告生成",
    WorkflowType.CLARIFICATION.value: "澄清",
}


class PlannerEnhancementError(Exception):
    """planner LLM 增强异常。"""


def _get_planner_model():
    """延迟创建 planner 专用模型。"""

    global _planner_model
    if _planner_model is None:
        model_name = os.getenv("MODEL_NAME")
        base_url = os.getenv("OPENAI_BASE_URL")
        api_key = os.getenv("OPENAI_API_KEY")
        if not model_name or not base_url or not api_key:
            raise PlannerEnhancementError("planner LLM 配置不完整")

        from langchain_openai import ChatOpenAI

        _planner_model = ChatOpenAI(
            model=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=0.1,
        )
    return _planner_model


def _extract_json_text(text: str) -> str:
    stripped = (text or "").strip()
    if not stripped:
        raise PlannerEnhancementError("模型未返回有效 JSON")
    block_match = _JSON_BLOCK_RE.search(stripped)
    if block_match:
        return block_match.group(1).strip()
    first_brace = stripped.find("{")
    last_brace = stripped.rfind("}")
    if first_brace != -1 and last_brace != -1 and first_brace < last_brace:
        return stripped[first_brace : last_brace + 1]
    raise PlannerEnhancementError("模型返回内容中未找到 JSON 对象")


def _loads_json_object(text: str) -> dict[str, Any]:
    parse_errors: list[str] = []
    for parser_name, parser in (("json", json.loads), ("literal", ast.literal_eval)):
        try:
            payload = parser(text)
        except Exception as exc:  # noqa: BLE001
            parse_errors.append(f"{parser_name}: {exc}")
            continue
        if isinstance(payload, dict):
            return payload
        parse_errors.append(f"{parser_name}: 返回值不是 JSON 对象")
    raise PlannerEnhancementError("模型 JSON 解析失败：" + "；".join(parse_errors))


def _parse_planning_payload(raw_text: str) -> dict[str, Any]:
    return _loads_json_object(_extract_json_text(raw_text))


async def _invoke_text_model(prompt: str, llm_model: Any | None = None) -> str:
    model = llm_model or _get_planner_model()
    response = await model.ainvoke(prompt)
    return getattr(response, "content", "") or str(response or "")


async def _invoke_json_model(prompt: str, llm_model: Any | None = None) -> dict[str, Any]:
    raw_text = await _invoke_text_model(prompt, llm_model)
    try:
        return _parse_planning_payload(raw_text)
    except PlannerEnhancementError as exc:
        repaired_text = await _invoke_text_model(build_planner_json_repair_prompt(raw_text, str(exc)), llm_model)
        try:
            return _parse_planning_payload(repaired_text)
        except PlannerEnhancementError as repair_exc:
            raise PlannerEnhancementError(f"{repair_exc}；原始错误：{exc}") from repair_exc


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _workflow_value(workflow_type: WorkflowType | str | None) -> str:
    if isinstance(workflow_type, WorkflowType):
        return workflow_type.value
    value = str(workflow_type or WorkflowType.FAULT_DIAGNOSIS.value).strip()
    return value if value in _WORKFLOW_LABELS else WorkflowType.FAULT_DIAGNOSIS.value


def _route_workflow_type(route_result: WorkflowRouteResult | dict[str, Any] | None) -> str:
    if route_result is None:
        return WorkflowType.FAULT_DIAGNOSIS.value
    if isinstance(route_result, dict):
        return _workflow_value(route_result.get("workflow_type"))
    return _workflow_value(route_result.workflow_type)


def _route_missing_slots(route_result: WorkflowRouteResult | dict[str, Any] | None) -> list[str]:
    if route_result is None:
        return []
    if isinstance(route_result, dict):
        return [str(item) for item in route_result.get("missing_slots") or [] if str(item).strip()]
    return [str(item) for item in route_result.missing_slots if str(item).strip()]


def _route_needs_report(route_result: WorkflowRouteResult | dict[str, Any] | None) -> bool:
    if route_result is None:
        return False
    if isinstance(route_result, dict):
        return bool(route_result.get("needs_report"))
    return bool(route_result.needs_report)


def _task_summary(message: str, workflow_type: str) -> str:
    label = _WORKFLOW_LABELS.get(workflow_type, "故障诊断")
    text = (message or "").strip()
    if not text:
        return f"执行{label}任务，并在回答中说明证据充分性。"
    return f"围绕用户请求“{text}”执行{label}任务，并在回答中说明证据充分性。"


def _evidence(
    evidence_type: str,
    description: str,
    *,
    required: bool,
    source_hint: str,
    missing_impact: str,
) -> PlanningEvidenceRequirement:
    return PlanningEvidenceRequirement(
        evidence_type=evidence_type,
        description=description,
        required=required,
        source_hint=source_hint,
        missing_impact=missing_impact,
    )


def _constraint(name: str, description: str, severity: str = "warning") -> PlanningConstraint:
    return PlanningConstraint(name=name, description=description, severity=severity)


def build_default_plan(
    message: str,
    user_identity: str,
    route_result: WorkflowRouteResult | dict[str, Any] | None = None,
) -> PlanningArtifact:
    """基于路由结果生成不依赖 LLM 的稳定计划。"""

    del user_identity
    workflow_type = _route_workflow_type(route_result)
    needs_report = _route_needs_report(route_result)
    missing_slots = _route_missing_slots(route_result)
    plan = PlanningArtifact(
        success=True,
        task_summary=_task_summary(message, workflow_type),
        workflow_type=workflow_type,
        diagnosis_goals=[],
        required_evidence=[],
        constraints=[],
        risk_flags=[],
        clarification_questions=[],
        success_criteria=[
            "最终回答必须区分事实、推断和建议。",
            "证据不足时必须明确说明缺口和置信度限制。",
        ],
        confidence="medium",
        fallback_used=False,
        error=None,
    )

    if workflow_type == WorkflowType.FAULT_DIAGNOSIS.value:
        plan.diagnosis_goals = [
            "确认当前请求涉及的设备、故障码、告警或异常现象。",
            "基于 SQL 实时数据判断异常与关键运行指标之间是否存在关联。",
            "结合知识库解释故障码或处置建议，并说明哪些内容只是候选原因。",
        ]
        plan.required_evidence = [
            _evidence(
                "sql",
                "近期告警记录和关键运行指标数据。",
                required=True,
                source_hint="real_data、device_alarm 或 device_fault_data",
                missing_impact="缺少实时数据时不能确认当前根因，只能给出候选判断。",
            ),
            _evidence(
                "knowledge_base",
                "故障码、报警含义或维修建议的手册解释。",
                required=False,
                source_hint="本地 FAISS 知识库",
                missing_impact="知识不足时需要降低处置建议置信度。",
            ),
        ]
        plan.constraints = [
            _constraint(
                "sql_first_for_current_cause",
                "涉及当前根因判断时，必须优先依据 SQL 实时数据。",
                "blocking",
            ),
            _constraint(
                "no_overclaim_without_evidence",
                "不得把缺少证据支撑的推断写成已确认事实。",
                "blocking",
            ),
        ]
        plan.risk_flags = [
            "时间范围不明确时，只能按最近记录分析并说明限制。",
            "关键指标缺失时，只能输出候选原因和下一步取证建议。",
        ]
        if needs_report:
            plan.constraints.append(
                _constraint(
                    "report_requires_evidence_sufficiency",
                    "报告生成前必须说明 SQL 与知识证据是否足以支撑正式结论。",
                    "warning",
                )
            )
            plan.success_criteria.append("如生成报告，必须说明报告证据是否充分。")

    elif workflow_type == WorkflowType.STATUS_INSPECTION.value:
        plan.diagnosis_goals = [
            "概览设备或系统当前运行状态。",
            "识别异常指标、风险等级和建议动作。",
        ]
        plan.required_evidence = [
            _evidence(
                "sql",
                "运行状态、关键指标和近期异常趋势数据。",
                required=True,
                source_hint="real_data 或 device_metric",
                missing_impact="缺少指标数据时不能确认当前运行状态。",
            )
        ]
        plan.constraints = [
            _constraint("inspection_not_root_cause", "状态巡检不应直接下根因诊断结论。", "warning")
        ]
        plan.risk_flags = ["指标范围或设备不明确时，需要说明巡检覆盖范围有限。"]

    elif workflow_type == WorkflowType.MANUAL_QA.value:
        plan.diagnosis_goals = [
            "回答故障码释义、操作步骤、安全注意事项或维修手册问题。",
            "说明回答依据来自知识库片段，知识不足时保守回答。",
        ]
        plan.required_evidence = [
            _evidence(
                "knowledge_base",
                "手册、知识库或维修说明片段。",
                required=True,
                source_hint="本地 FAISS 知识库",
                missing_impact="知识不足时不得编造操作步骤。",
            )
        ]
        plan.constraints = [
            _constraint("manual_qa_no_sql_required", "手册问答默认不要求 SQL 实时数据。", "blocking")
        ]
        plan.risk_flags = ["知识库未命中时必须明确说明无法可靠回答。"]

    elif workflow_type == WorkflowType.REPORT_GENERATION.value:
        plan.diagnosis_goals = [
            "读取上游诊断、巡检或复核产物。",
            "基于既有产物整理报告，不重新执行完整诊断。",
        ]
        plan.required_evidence = [
            _evidence(
                "artifact",
                "上游 workflow artifact、分析结论和证据快照。",
                required=True,
                source_hint="WorkflowArtifactEnvelope.payload",
                missing_impact="缺少上游产物时不能生成正式报告。",
            )
        ]
        plan.constraints = [
            _constraint("report_uses_upstream_artifact", "报告生成必须依赖上游 artifact。", "blocking"),
            _constraint("no_full_rediagnosis", "报告生成场景不重新执行完整故障诊断。", "blocking"),
        ]
        plan.risk_flags = ["上游结构化产物缺失时，报告生成无法继续。"]

    elif workflow_type == WorkflowType.CLARIFICATION.value:
        questions = [f"请补充 {slot}。" for slot in missing_slots] or ["请补充设备、故障码、指标或时间范围等关键信息。"]
        plan.diagnosis_goals = [
            "识别当前请求缺失的关键信息。",
            "生成最小必要的澄清问题，帮助用户进入正确场景。",
        ]
        plan.required_evidence = [
            _evidence(
                "user_input",
                "用户补充的设备、故障码、指标或时间范围。",
                required=True,
                source_hint="用户澄清回复",
                missing_impact="缺少关键槽位时不应进入 SQL、报告或复核流程。",
            )
        ]
        plan.constraints = [
            _constraint("clarification_only", "澄清流只提问，不直接下诊断结论。", "blocking")
        ]
        plan.clarification_questions = questions
        plan.risk_flags = ["信息不足时强行执行主链路会导致错误诊断或错误报告。"]

    return validate_planning_boundary(plan)


def validate_planning_boundary(plan: PlanningArtifact | dict[str, Any]) -> PlanningArtifact:
    """校验 planner 产物没有越过当前场景边界。"""

    artifact = plan if isinstance(plan, PlanningArtifact) else PlanningArtifact.model_validate(plan)
    workflow_type = _workflow_value(artifact.workflow_type)
    evidence_types = {item.evidence_type for item in artifact.required_evidence if item.required}

    if workflow_type == WorkflowType.FAULT_DIAGNOSIS.value and "sql" not in evidence_types:
        raise ValueError("故障诊断计划必须包含必需 SQL 证据。")
    if workflow_type == WorkflowType.MANUAL_QA.value and "sql" in evidence_types:
        raise ValueError("手册问答计划不应要求必需 SQL 证据。")
    if workflow_type == WorkflowType.REPORT_GENERATION.value and "artifact" not in evidence_types:
        raise ValueError("报告生成计划必须依赖上游 artifact。")
    if workflow_type == WorkflowType.CLARIFICATION.value and not artifact.clarification_questions:
        raise ValueError("澄清计划必须输出澄清问题。")
    return artifact


async def create_planning_artifact(
    message: str,
    user_identity: str,
    route_result: WorkflowRouteResult | dict[str, Any] | None = None,
    *,
    enable_llm: bool = True,
    llm_model: Any | None = None,
    llm_plan_provider: Callable[[PlanningArtifact], Any] | None = None,
) -> PlanningArtifact:
    """创建 planner 产物；LLM 增强失败时回退到规则计划。"""

    default_plan = build_default_plan(message, user_identity, route_result)
    if not enable_llm:
        return default_plan
    if llm_plan_provider is None and llm_model is None and os.getenv("PYTEST_CURRENT_TEST"):
        return default_plan

    try:
        if llm_plan_provider is None:
            prompt = build_planner_json_prompt(
                message=message,
                user_identity=user_identity,
                route_result=route_result,
                default_plan=default_plan,
            )
            enhanced_payload = await _invoke_json_model(prompt, llm_model)
        else:
            enhanced_payload = await _maybe_await(llm_plan_provider(default_plan))
            if isinstance(enhanced_payload, str):
                enhanced_payload = _parse_planning_payload(enhanced_payload)
        enhanced_plan = validate_planning_boundary(enhanced_payload)
        enhanced_plan.fallback_used = False
        enhanced_plan.error = None
        return enhanced_plan
    except Exception as exc:  # noqa: BLE001
        default_plan.fallback_used = True
        default_plan.error = f"planner 增强失败，已回退规则计划：{exc}"
        return default_plan
