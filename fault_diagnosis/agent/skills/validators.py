"""Pure contract validators for Agent Engine V2 skill packages."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from .registry import SkillMetadata


class SkillValidationIssue(BaseModel):
    code: str
    message: str
    severity: Literal["warning", "error"] = "error"
    path: str = ""


class SkillValidationResult(BaseModel):
    issues: list[SkillValidationIssue] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)


class SkillValidationContext(BaseModel):
    """Read-only summaries supplied to a skill validator."""

    skill_name: str
    metadata: SkillMetadata
    inputs: dict[str, Any] = Field(default_factory=dict)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    plan: dict[str, Any] = Field(default_factory=dict)


SkillValidator = Callable[[SkillValidationContext], SkillValidationResult]


def noop_validator(_: SkillValidationContext) -> SkillValidationResult:
    return SkillValidationResult()


@dataclass(frozen=True)
class SkillValidatorRegistry:
    input: SkillValidator = noop_validator
    evidence: SkillValidator = noop_validator
    output: SkillValidator = noop_validator

    def validate(self, kind: Literal["input", "evidence", "output"], context: SkillValidationContext) -> SkillValidationResult:
        return getattr(self, kind)(context)

    def kinds(self) -> tuple[str, str, str]:
        return ("input", "evidence", "output")


def merge_results(*results: SkillValidationResult) -> SkillValidationResult:
    return SkillValidationResult(issues=[issue for result in results for issue in result.issues])


def validate_required_slots(context: SkillValidationContext) -> SkillValidationResult:
    missing = [
        slot
        for slot in context.metadata.slot_policy.required
        if not _has_value(context.inputs.get(slot))
    ]
    return _missing_result("missing_required_slot", "inputs", missing)


def validate_required_evidence(context: SkillValidationContext) -> SkillValidationResult:
    searchable = " ".join(_flatten_text(item) for item in context.evidence).lower()
    missing = [
        requirement
        for requirement in context.metadata.evidence_policy.required
        if requirement.lower() not in searchable
    ]
    return _missing_result("missing_required_evidence", "evidence", missing)


def validate_required_output_fields(context: SkillValidationContext) -> SkillValidationResult:
    missing = [
        field
        for field in context.metadata.output_contract.required_fields
        if not _path_has_value(context.output, field)
    ]
    return _missing_result("missing_required_output_field", "output", missing)


def validate_forbidden_claims(context: SkillValidationContext) -> SkillValidationResult:
    output_text = _flatten_text(context.output).lower()
    issues = [
        SkillValidationIssue(
            code="forbidden_claim",
            message=f"Output contains forbidden claim marker: {claim}",
            path="output",
        )
        for claim in context.metadata.output_contract.forbidden_claims
        if claim.lower() in output_text
    ]
    return SkillValidationResult(issues=issues)


def validate_forbidden_actions(context: SkillValidationContext) -> SkillValidationResult:
    action_text = " ".join(
        (_flatten_text(context.inputs), _flatten_text(context.plan), _flatten_text(context.output))
    ).lower()
    issues = [
        SkillValidationIssue(
            code="forbidden_action",
            message=f"Skill contract forbids action: {action}",
            path="plan",
        )
        for action in context.metadata.safety_contract.forbidden_actions
        if _contains_action(action_text, action)
    ]
    return SkillValidationResult(issues=issues)


def validate_draft_and_confirmation(context: SkillValidationContext) -> SkillValidationResult:
    contract = context.metadata.safety_contract
    values = {**context.inputs, **context.output}
    issues: list[SkillValidationIssue] = []
    if contract.draft_only and values.get("draft_only") is not True:
        issues.append(
            SkillValidationIssue(
                code="draft_only_required",
                message="Skill output/action must remain draft-only.",
                path="output.draft_only",
            )
        )
    if contract.manual_confirmation_required and values.get("manual_confirmation_required") is not True:
        issues.append(
            SkillValidationIssue(
                code="manual_confirmation_required",
                message="Manual confirmation must be explicitly required.",
                path="output.manual_confirmation_required",
            )
        )
    return SkillValidationResult(issues=issues)


def issue(code: str, message: str, *, path: str = "", severity: Literal["warning", "error"] = "error") -> SkillValidationResult:
    return SkillValidationResult(
        issues=[SkillValidationIssue(code=code, message=message, path=path, severity=severity)]
    )


def _missing_result(code: str, path: str, missing: list[str]) -> SkillValidationResult:
    return SkillValidationResult(
        issues=[
            SkillValidationIssue(
                code=code,
                message=f"Missing contract requirement: {item}",
                path=f"{path}.{item}",
            )
            for item in missing
        ]
    )


def _has_value(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _path_has_value(payload: dict[str, Any], path: str) -> bool:
    value: Any = payload
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return False
        value = value[part]
    return _has_value(value)


def _flatten_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(f"{key} {_flatten_text(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten_text(item) for item in value)
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        return str(value)


def _contains_action(text: str, action: str) -> bool:
    text_tokens = re.findall(r"[a-z0-9]+", text.lower())
    action_tokens = re.findall(r"[a-z0-9]+", action.lower())
    if not action_tokens:
        return False
    width = len(action_tokens)
    return any(text_tokens[index : index + width] == action_tokens for index in range(len(text_tokens) - width + 1))
