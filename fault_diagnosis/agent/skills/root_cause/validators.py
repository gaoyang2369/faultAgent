"""Contract checks for evidence-backed root-cause candidates."""

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
    base = validate_required_output_fields(context)
    candidates = context.output.get("root_cause_candidates")
    if not isinstance(candidates, list) or not candidates:
        return merge_results(
            base,
            issue("root_cause_candidates_required", "Root-cause output must contain candidate objects.", path="output"),
        )
    results = [base]
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            results.append(issue("invalid_root_cause_candidate", "Candidate must be an object.", path=f"output.root_cause_candidates.{index}"))
            continue
        for field in ("supporting_evidence", "missing_evidence", "confidence"):
            if field not in candidate or candidate[field] in (None, "", []):
                results.append(
                    issue(
                        "incomplete_root_cause_candidate",
                        f"Root-cause candidate is missing {field}.",
                        path=f"output.root_cause_candidates.{index}.{field}",
                    )
                )
    return merge_results(*results)
