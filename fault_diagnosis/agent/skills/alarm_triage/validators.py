"""Contract checks for SQL plus manual alarm triage."""

from fault_diagnosis.agent.skills.validators import (
    SkillValidationContext,
    SkillValidationResult,
    issue,
    merge_results,
    validate_forbidden_actions,
    validate_required_output_fields,
    validate_required_slots,
)


def validate_input(context: SkillValidationContext) -> SkillValidationResult:
    return merge_results(validate_required_slots(context), validate_forbidden_actions(context))


def validate_evidence(context: SkillValidationContext) -> SkillValidationResult:
    text = str(context.evidence).lower()
    results: list[SkillValidationResult] = []
    if "sql" not in text:
        results.append(issue("missing_sql_evidence", "Alarm triage requires SQL runtime evidence.", path="evidence"))
    if not any(marker in text for marker in ("rag", "kb", "manual")):
        results.append(issue("missing_rag_evidence", "Alarm triage requires RAG/manual evidence.", path="evidence"))
    return merge_results(*results)


def validate_output(context: SkillValidationContext) -> SkillValidationResult:
    result = validate_required_output_fields(context)
    evidence_result = validate_evidence(context)
    if evidence_result.passed:
        return result
    partial = context.output.get("status") == "partial" or context.output.get("partial") is True
    disclosed = bool(context.output.get("unknowns") or context.output.get("missing_evidence"))
    if partial and disclosed:
        return result
    return merge_results(
        result,
        issue(
            "partial_disclosure_required",
            "Missing SQL or RAG evidence requires partial status and explicit disclosure.",
            path="output",
        ),
    )
