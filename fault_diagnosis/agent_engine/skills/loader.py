"""Progressive skill package loader for Agent Engine V2."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from .registry import SkillMetadata, SkillRegistry


class LoadedSkill(BaseModel):
    """Loaded prompt/schema/examples for a selected skill."""

    metadata: SkillMetadata
    input_schema: dict[str, Any] = Field(default_factory=dict)
    examples: list[dict[str, Any]] = Field(default_factory=list)
    prompt: str = ""
    loaded_files: list[str] = Field(default_factory=list)


class SkillLoader:
    """Load only the packages selected by SkillRouter."""

    def __init__(self, registry: SkillRegistry | None = None) -> None:
        self.registry = registry or SkillRegistry()

    def load(self, selected_skills: list[str]) -> dict[str, LoadedSkill]:
        discovered = self.registry.discover()
        loaded: dict[str, LoadedSkill] = {}
        for skill_name in selected_skills:
            metadata = discovered.get(skill_name)
            if metadata is None:
                continue
            package_path = Path(metadata.package_path)
            loaded[skill_name] = LoadedSkill(
                metadata=metadata,
                input_schema=_read_yaml_dict(package_path / "schema.yaml"),
                examples=_read_yaml_list(package_path / "examples.yaml"),
                prompt=_read_text(package_path / "prompt.md"),
                loaded_files=_existing_relative_files(package_path, skill_name),
            )
        return loaded


def _read_yaml_dict(path: Path) -> dict[str, Any]:
    data = _read_yaml(path)
    return data if isinstance(data, dict) else {}


def _read_yaml_list(path: Path) -> list[dict[str, Any]]:
    data = _read_yaml(path)
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _read_yaml(path: Path) -> Any:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _existing_relative_files(package_path: Path, skill_name: str) -> list[str]:
    names = ["skill.yaml", "schema.yaml", "examples.yaml", "prompt.md"]
    return [f"{skill_name}/{name}" for name in names if (package_path / name).exists()]
