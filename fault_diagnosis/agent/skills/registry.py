"""Skill registry for Agent Engine V2 planning."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator


class SlotPolicy(BaseModel):
    required: list[str] = Field(default_factory=list)
    optional: list[str] = Field(default_factory=list)
    inherit_allowed: list[str] = Field(default_factory=list)
    inherit_forbidden: list[str] = Field(default_factory=list)


class EvidencePolicy(BaseModel):
    required: list[str] = Field(default_factory=list)
    optional: list[str] = Field(default_factory=list)
    freshness_policy: str = ""
    missing_evidence_behavior: str = ""


class NodePolicy(BaseModel):
    required_nodes: list[str] = Field(default_factory=list)
    optional_nodes: list[str] = Field(default_factory=list)
    forbidden_nodes: list[str] = Field(default_factory=list)


class OutputContract(BaseModel):
    variants: list[str] = Field(default_factory=list)
    required_fields: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)


class SafetyContract(BaseModel):
    forbidden_actions: list[str] = Field(default_factory=list)
    manual_confirmation_required: bool = False
    draft_only: bool = False


class CompositionPolicy(BaseModel):
    can_compose_with: list[str] = Field(default_factory=list)
    preferred_primary_when_composed: str = ""
    dependency_rules: list[dict[str, Any]] = Field(default_factory=list)


class SkillMetadata(BaseModel):
    """Declarative skill package metadata loaded from skill.yaml."""

    name: str
    version: str = "0.1.0"
    description: str = ""
    trigger_intents: list[str] = Field(default_factory=list)
    required_slots: list[str] = Field(default_factory=list)
    optional_slots: list[str] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    allowed_nodes: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    risk_level: str = "low"
    output_variants: list[str] = Field(default_factory=list)
    fallback_policy: str = ""
    slot_policy: SlotPolicy = Field(default_factory=SlotPolicy)
    evidence_policy: EvidencePolicy = Field(default_factory=EvidencePolicy)
    node_policy: NodePolicy = Field(default_factory=NodePolicy)
    output_contract: OutputContract = Field(default_factory=OutputContract)
    safety_contract: SafetyContract = Field(default_factory=SafetyContract)
    composition: CompositionPolicy = Field(default_factory=CompositionPolicy)
    package_path: str = ""

    @model_validator(mode="after")
    def project_legacy_fields(self) -> "SkillMetadata":
        """Keep old skill.yaml fields authoritative when new contracts are absent."""

        if not self.slot_policy.required:
            self.slot_policy.required = list(self.required_slots)
        if not self.slot_policy.optional:
            self.slot_policy.optional = list(self.optional_slots)
        if not self.evidence_policy.required:
            self.evidence_policy.required = list(self.required_evidence)
        if not self.node_policy.required_nodes:
            self.node_policy.required_nodes = list(self.allowed_nodes)
        if not self.output_contract.variants:
            self.output_contract.variants = list(self.output_variants)

        self.required_slots = _dedupe([*self.required_slots, *self.slot_policy.required])
        self.optional_slots = _dedupe([*self.optional_slots, *self.slot_policy.optional])
        self.required_evidence = _dedupe([*self.required_evidence, *self.evidence_policy.required])
        self.allowed_nodes = _dedupe([*self.allowed_nodes, *self.node_policy.required_nodes, *self.node_policy.optional_nodes])
        self.output_variants = _dedupe([*self.output_variants, *self.output_contract.variants])
        self.forbidden_tools = _dedupe(self.forbidden_tools)
        self.allowed_tools = _dedupe(self.allowed_tools)
        return self


class SkillRegistry:
    """Discover skill packages without loading prompt/schema/example payloads."""

    def __init__(self, skill_root: Path | None = None) -> None:
        self.skill_root = skill_root or Path(__file__).parent

    def discover(self) -> dict[str, SkillMetadata]:
        skills: dict[str, SkillMetadata] = {}
        for skill_file in sorted(self.skill_root.glob("*/skill.yaml")):
            data = _read_yaml(skill_file)
            if not isinstance(data, dict):
                continue
            metadata = SkillMetadata(**data, package_path=str(skill_file.parent))
            skills[metadata.name] = metadata
        return skills

    def get(self, name: str) -> SkillMetadata | None:
        return self.discover().get(name)


def _read_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
