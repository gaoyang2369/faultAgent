from __future__ import annotations

from pathlib import Path

import yaml

from fault_diagnosis.agent.skills import SkillMetadata


SKILL_ROOT = Path("fault_diagnosis/agent/skills")


def test_all_skill_yaml_files_are_safe_loadable_multiline_yaml() -> None:
    paths = sorted(
        path
        for pattern in ("*/skill.yaml", "*/schema.yaml", "*/examples.yaml")
        for path in SKILL_ROOT.glob(pattern)
    )
    assert paths
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert len(text.splitlines()) > 1, f"{path} must be standard multiline YAML"
        assert yaml.safe_load(text) is not None, f"{path} must contain a YAML document"


def test_all_skill_metadata_files_pass_pydantic_validation() -> None:
    for path in sorted(SKILL_ROOT.glob("*/skill.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        metadata = SkillMetadata.model_validate({**data, "package_path": str(path.parent)})
        assert metadata.name == path.parent.name
        assert metadata.slot_policy.required == data["slot_policy"]["required"]
        assert metadata.node_policy.required_nodes
        assert metadata.output_contract.variants
