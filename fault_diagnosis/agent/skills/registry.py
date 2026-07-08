"""Skill registry for Agent Engine V2 sidecar planning."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


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
    package_path: str = ""


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
