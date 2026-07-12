"""Contract checks for clarification output."""

from fault_diagnosis.agent.skills.validators import (
    SkillValidationContext,
    SkillValidationResult,
    merge_results,
    validate_forbidden_actions,
    validate_required_output_fields,
    validate_required_slots,
)


def validate_input(context: SkillValidationContext) -> SkillValidationResult:
    return merge_results(validate_required_slots(context), validate_forbidden_actions(context))


def validate_evidence(_: SkillValidationContext) -> SkillValidationResult:
    return SkillValidationResult()


def validate_output(context: SkillValidationContext) -> SkillValidationResult:
    return validate_required_output_fields(context)
