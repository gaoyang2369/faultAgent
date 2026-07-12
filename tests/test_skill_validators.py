from __future__ import annotations

import pytest

from fault_diagnosis.agent.skills import SkillLoader, SkillRegistry, SkillValidationContext
from fault_diagnosis.agent.skills.loader import _validate_validator_source


def _loaded(name: str):
    return SkillLoader().load([name])[name]


def _context(name: str, **kwargs) -> SkillValidationContext:
    loaded = _loaded(name)
    return SkillValidationContext(skill_name=name, metadata=loaded.metadata, **kwargs)


def test_every_skill_loads_input_evidence_and_output_validators() -> None:
    names = list(SkillRegistry().discover())
    loaded = SkillLoader().load(names)
    assert set(loaded) == set(names)
    for skill in loaded.values():
        assert skill.validators.kinds() == ("input", "evidence", "output")
        assert skill.loaded_files[-1].endswith("validators.py")


def test_validator_module_cannot_import_or_call_runtime_tools(tmp_path) -> None:
    path = tmp_path / "validators.py"
    path.write_text(
        "from fault_diagnosis.domain.security.tool_gateway import authorize_tool_call\n"
        "def validate_input(context):\n"
        "    return authorize_tool_call(context)\n",
        encoding="utf-8",
    )
    with pytest.raises(ImportError, match="not contract-safe"):
        _validate_validator_source(path, "unsafe_skill")


def test_fault_code_validator_rejects_missing_source_page() -> None:
    loaded = _loaded("fault_code_explain")
    context = _context(
        "fault_code_explain",
        inputs={"fault_code_refs": ["A07089"]},
        evidence=[
            {
                "manual_reference": "G120 manual",
                "exact_code_match": True,
                "source_file": "g120.pdf",
            }
        ],
    )
    result = loaded.validators.evidence(context)
    assert result.passed is False
    assert any(issue.path == "evidence.source_page" for issue in result.issues)


def test_runtime_validator_rejects_missing_freshness() -> None:
    loaded = _loaded("runtime_status")
    context = _context(
        "runtime_status",
        inputs={"device_refs": ["J1"]},
        evidence=[
            {
                "asset_identity": "J1",
                "data_source": "real_data_01",
                "sample_window": "1h",
                "latest_sample_time": "2026-07-10T10:00:00+08:00",
                "row_count": 10,
                "runtime_status": "abnormal",
            }
        ],
    )
    result = loaded.validators.evidence(context)
    assert result.passed is False
    assert any(issue.path == "evidence.freshness" for issue in result.issues)


def test_root_cause_validator_rejects_unbacked_single_cause() -> None:
    loaded = _loaded("root_cause")
    context = _context(
        "root_cause",
        output={"root_cause_candidates": [{"candidate": "overload", "confidence": 0.9}]},
    )
    result = loaded.validators.output(context)
    assert result.passed is False
    assert {issue.path.rsplit(".", 1)[-1] for issue in result.issues} >= {"supporting_evidence", "missing_evidence"}


def test_workorder_validator_rejects_non_draft_or_dispatch_action() -> None:
    loaded = _loaded("workorder_decision")
    context = _context(
        "workorder_decision",
        inputs={
            "device_refs": ["J1"],
            "requested_action": "workorder.dispatch",
            "draft_only": False,
            "manual_confirmation_required": True,
        },
    )
    result = loaded.validators.input(context)
    assert result.passed is False
    assert {issue.code for issue in result.issues} >= {"forbidden_action", "draft_only_required"}
