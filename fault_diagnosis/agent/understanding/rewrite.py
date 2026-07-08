"""Request rewrite builder for Agent Engine V2."""

from __future__ import annotations

from ..contracts import ContextFrame, IntentFrame, RewriteFrame


_REPORT_CONTEXT_WORDS = ("刚才", "刚刚", "上一轮", "上一条", "上一次", "前面的结果", "诊断结果", "巡检结果")


class RewriteFrameBuilder:
    """Build query rewrites without calling tools or mutating context."""

    def build(
        self,
        raw_message: str,
        *,
        intent_frame: IntentFrame,
        context_frame: ContextFrame | None = None,
    ) -> RewriteFrame:
        normalized = intent_frame.normalized_message or (raw_message or "").strip()
        context_frame = context_frame or ContextFrame()
        sub_intents = set(intent_frame.sub_intents)
        device = intent_frame.device_refs[0] if intent_frame.device_refs else ""
        fault_code = intent_frame.fault_code_refs[0] if intent_frame.fault_code_refs else ""
        references_previous = _references_previous_result(normalized) or bool(context_frame.referenced_artifact_id)
        explicit_device_switch = bool(device and _looks_like_device_switch(normalized))

        if "generate_report" in sub_intents and references_previous:
            rewrite_reason = "用户要求基于刚才结果生成报告，保留上一轮 artifact 或诊断结果引用意图。"
            artifact_note = (
                f" artifact={context_frame.referenced_artifact_id}"
                if context_frame.referenced_artifact_id
                else " artifact=previous_result"
            )
            user_rewrite = f"基于当前线程上一轮诊断结果生成报告；{artifact_note}"
        elif explicit_device_switch:
            rewrite_reason = f"用户显式切换到设备 {device}，本轮不静默继承上一设备。"
            user_rewrite = f"查询 {device} 当前运行状态"
        elif device and "check_current_status" in sub_intents:
            rewrite_reason = "用户要求查询指定设备当前状态。"
            user_rewrite = f"查询 {device} 当前运行状态"
        else:
            rewrite_reason = "规则 fallback 保留用户原始问题。"
            user_rewrite = normalized

        retrieval_queries = _retrieval_queries(
            user_rewrite=user_rewrite,
            fault_code=fault_code,
            sub_intents=sub_intents,
            references_previous=references_previous,
        )
        sql_question = _sql_question(device=device, user_rewrite=user_rewrite, sub_intents=sub_intents)
        manual_query = _manual_query(fault_code=fault_code, sub_intents=sub_intents)
        kg_query = _kg_query(fault_code=fault_code, sub_intents=sub_intents)
        staleness_note = _staleness_note(sub_intents=sub_intents, references_previous=references_previous)

        return RewriteFrame(
            user_rewrite=user_rewrite,
            rewrite_reason=rewrite_reason,
            retrieval_queries=retrieval_queries,
            sql_question=sql_question,
            manual_query=manual_query,
            kg_query=kg_query,
            staleness_note=staleness_note,
        )


def _references_previous_result(text: str) -> bool:
    compact = (text or "").replace(" ", "")
    return any(word in compact for word in _REPORT_CONTEXT_WORDS)


def _looks_like_device_switch(text: str) -> bool:
    compact = (text or "").replace(" ", "")
    return any(word in compact for word in ("那", "换", "切换", "再看", "看看"))


def _retrieval_queries(
    *,
    user_rewrite: str,
    fault_code: str,
    sub_intents: set[str],
    references_previous: bool,
) -> list[str]:
    queries: list[str] = [user_rewrite] if user_rewrite else []
    if fault_code and "explain_fault_code" in sub_intents:
        queries.append(f"{fault_code} 故障码 含义 原因 处理")
    if references_previous:
        queries.append("当前线程上一轮诊断结果 artifact")
    return _dedupe(queries)


def _sql_question(*, device: str, user_rewrite: str, sub_intents: set[str]) -> str:
    if device and "check_current_status" in sub_intents:
        return f"{device} 当前或最近运行状态是否仍存在异常"
    if "device_action_request" in sub_intents:
        return "高风险动作请求需要先确认当前设备状态，但本阶段不执行查询"
    return ""


def _manual_query(*, fault_code: str, sub_intents: set[str]) -> str:
    if fault_code and "explain_fault_code" in sub_intents:
        return f"{fault_code} 故障码含义、触发原因和处置步骤"
    return ""


def _kg_query(*, fault_code: str, sub_intents: set[str]) -> str:
    if fault_code and "explain_fault_code" in sub_intents:
        return f"{fault_code} fault code explanation"
    return ""


def _staleness_note(*, sub_intents: set[str], references_previous: bool) -> str:
    if references_previous and "generate_report" in sub_intents:
        return "报告生成前需要校验上一轮 artifact 的权限、设备范围和证据时效。"
    if sub_intents.intersection({"decide_workorder", "create_workorder_draft", "dispatch_workorder"}):
        return "工单判断或草稿生成前需要刷新或披露当前状态时效。"
    return ""


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in values if item))
