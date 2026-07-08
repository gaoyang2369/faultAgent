from __future__ import annotations

from fault_diagnosis import config
from fault_diagnosis.agent_engine.flags import effective_skill_mode, load_agent_engine_flags, should_build_v2_compare


def test_skill_flags_default_to_legacy(monkeypatch) -> None:
    monkeypatch.setattr(config, "AGENT_ENGINE_VERSION", "legacy")
    for name in (
        "AGENT_ENGINE_V2_SKILL_FAULT_CODE_EXPLAIN",
        "AGENT_ENGINE_V2_SKILL_RUNTIME_STATUS",
    ):
        monkeypatch.delenv(name, raising=False)

    flags = load_agent_engine_flags()

    assert flags.engine_mode == "legacy"
    assert effective_skill_mode("fault_code_explain", flags=flags) == "legacy"
    assert should_build_v2_compare(flags=flags) is False


def test_global_shadow_enables_compare_but_allows_skill_rollback(monkeypatch) -> None:
    monkeypatch.setattr(config, "AGENT_ENGINE_VERSION", "v2_shadow")
    monkeypatch.delenv("AGENT_ENGINE_V2_SKILL_RUNTIME_STATUS", raising=False)
    monkeypatch.setenv("AGENT_ENGINE_V2_SKILL_FAULT_CODE_EXPLAIN", "legacy")

    flags = load_agent_engine_flags()

    assert should_build_v2_compare(flags=flags) is True
    assert effective_skill_mode("runtime_status", flags=flags) == "shadow"
    assert effective_skill_mode("fault_code_explain", flags=flags) == "legacy"


def test_v2_global_requires_explicit_skill_v2(monkeypatch) -> None:
    monkeypatch.setattr(config, "AGENT_ENGINE_VERSION", "v2")
    monkeypatch.setenv("AGENT_ENGINE_V2_SKILL_FAULT_CODE_EXPLAIN", "v2")
    monkeypatch.delenv("AGENT_ENGINE_V2_SKILL_RUNTIME_STATUS", raising=False)

    flags = load_agent_engine_flags()

    assert effective_skill_mode("fault_code_explain", flags=flags) == "v2"
    assert effective_skill_mode("runtime_status", flags=flags) == "legacy"
