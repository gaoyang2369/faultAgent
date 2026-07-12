"""Progressive skill package loader for Agent Engine V2."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .registry import SkillMetadata, SkillRegistry
from .validators import SkillValidatorRegistry, noop_validator


class LoadedSkill(BaseModel):
    """Loaded prompt/schema/examples for a selected skill."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    metadata: SkillMetadata
    input_schema: dict[str, Any] = Field(default_factory=dict)
    examples: list[dict[str, Any]] = Field(default_factory=list)
    prompt: str = ""
    validators: SkillValidatorRegistry = Field(default_factory=SkillValidatorRegistry)
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
                validators=_load_validators(package_path, skill_name),
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
    names = ["skill.yaml", "schema.yaml", "examples.yaml", "prompt.md", "validators.py"]
    return [f"{skill_name}/{name}" for name in names if (package_path / name).exists()]


def _load_validators(package_path: Path, skill_name: str) -> SkillValidatorRegistry:
    validator_path = package_path / "validators.py"
    if not validator_path.exists():
        return SkillValidatorRegistry()

    _validate_validator_source(validator_path, skill_name)
    module_name = f"fault_diagnosis.agent.skills.{skill_name}._contract_validators"
    spec = importlib.util.spec_from_file_location(module_name, validator_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load validators for skill {skill_name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    validators = {
        kind: getattr(module, f"validate_{kind}", noop_validator)
        for kind in ("input", "evidence", "output")
    }
    for kind, validator in validators.items():
        if not callable(validator):
            raise TypeError(f"Skill {skill_name} validate_{kind} must be callable")
    return SkillValidatorRegistry(**validators)


def _validate_validator_source(path: Path, skill_name: str) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    allowed_imports = {
        "__future__",
        "typing",
        "fault_diagnosis.agent.skills.validators",
    }
    blocked_calls = {
        "authorize_tool_call",
        "invoke_sql_tool",
        "query_knowledge_base",
        "run_tool",
        "tool_gateway",
    }
    for node in tree.body:
        if isinstance(node, ast.Import):
            modules = {alias.name for alias in node.names}
            if not modules.issubset(allowed_imports):
                raise ImportError(f"Skill {skill_name} validator imports are not contract-safe: {sorted(modules)}")
        elif isinstance(node, ast.ImportFrom):
            if str(node.module or "") not in allowed_imports:
                raise ImportError(f"Skill {skill_name} validator import is not contract-safe: {node.module}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        else:
            raise ImportError(f"Skill {skill_name} validators.py may only declare validators and safe imports")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_name = _call_name(node.func)
        if call_name in blocked_calls:
            raise ImportError(f"Skill {skill_name} validator cannot call runtime tool API: {call_name}")


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""
