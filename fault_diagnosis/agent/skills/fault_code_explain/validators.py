"""Contract checks for fault-code explanation artifacts."""

from fault_diagnosis.agent.skills.validators import (
    SkillValidationContext,
    SkillValidationResult,
    issue,
    merge_results,
    validate_forbidden_actions,
    validate_forbidden_claims,
    validate_required_evidence,
    validate_required_output_fields,
    validate_required_slots,
)


def validate_input(context: SkillValidationContext) -> SkillValidationResult:
    return merge_results(validate_required_slots(context), validate_forbidden_actions(context))


def validate_evidence(context: SkillValidationContext) -> SkillValidationResult:
    return validate_required_evidence(context)


def validate_output(context: SkillValidationContext) -> SkillValidationResult:
    result = merge_results(validate_required_output_fields(context), validate_forbidden_claims(context))
    text = str(context.output).lower()
    if any(marker in text for marker in ("当前实时", "currently active", "正在报警", "当前正在")):
        return merge_results(
            result,
            issue(
                "realtime_status_claim_forbidden",
                "Fault-code explanation cannot claim the device's current realtime status.",
                path="output",
            ),
        )
    return result
