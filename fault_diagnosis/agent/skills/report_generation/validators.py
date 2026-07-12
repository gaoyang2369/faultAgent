"""Contract checks for report source and freshness handling."""

from fault_diagnosis.agent.skills.validators import (
    SkillValidationContext,
    SkillValidationResult,
    issue,
    merge_results,
    validate_forbidden_actions,
    validate_required_evidence,
    validate_required_output_fields,
    validate_required_slots,
)


def validate_input(context: SkillValidationContext) -> SkillValidationResult:
    return merge_results(validate_required_slots(context), validate_forbidden_actions(context))


def validate_evidence(context: SkillValidationContext) -> SkillValidationResult:
    return validate_required_evidence(context)


def validate_output(context: SkillValidationContext) -> SkillValidationResult:
    result = validate_required_output_fields(context)
    source_status = str(context.output.get("source_artifact_status", "")).lower()
    if source_status not in {"stale", "expired"}:
        return result
    refreshed = context.output.get("refreshed") is True
    disclosed = bool(context.output.get("freshness_disclosure"))
    if refreshed or disclosed:
        return result
    return merge_results(
        result,
        issue(
            "stale_report_source_undisclosed",
            "Stale report evidence must be refreshed or disclosed.",
            path="output.freshness_disclosure",
        ),
    )
