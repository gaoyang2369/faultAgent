"""Contract checks for draft-only work-order proposals."""

from fault_diagnosis.agent.skills.validators import (
    SkillValidationContext,
    SkillValidationResult,
    merge_results,
    validate_draft_and_confirmation,
    validate_forbidden_actions,
    validate_required_evidence,
    validate_required_output_fields,
    validate_required_slots,
)


def validate_input(context: SkillValidationContext) -> SkillValidationResult:
    return merge_results(
        validate_required_slots(context),
        validate_forbidden_actions(context),
        validate_draft_and_confirmation(context),
    )


def validate_evidence(context: SkillValidationContext) -> SkillValidationResult:
    return validate_required_evidence(context)


def validate_output(context: SkillValidationContext) -> SkillValidationResult:
    return merge_results(
        validate_required_output_fields(context),
        validate_forbidden_actions(context),
        validate_draft_and_confirmation(context),
    )
