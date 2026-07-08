from __future__ import annotations

from fault_diagnosis import config
from fault_diagnosis.agent_engine.flags import (
    effective_skill_mode,
    load_agent_engine_flags,
    should_build_v2_compare,
)


def test_agent_engine_defaults_to_v2(monkeypatch) -> None:
    monkeypatch.setattr(config, "AGENT_ENGINE_VERSION", "v2")
    monkeypatch.delenv("AGENT_ENGINE_V2_SKILL_RUNTIME_STATUS", raising=False)

    flags = load_agent_engine_flags()

    assert flags.engine_mode == "v2"
    assert effective_skill_mode("runtime_status", flags=flags) == "v2"
    assert should_build_v2_compare(flags=flags) is False


def test_retired_legacy_mode_resolves_to_v2(monkeypatch) -> None:
    monkeypatch.setattr(config, "AGENT_ENGINE_VERSION", "legacy")
    monkeypatch.setenv("AGENT_ENGINE_V2_SKILL_FAULT_CODE_EXPLAIN", "v2")

    flags = load_agent_engine_flags()

    assert flags.engine_mode == "v2"
    assert effective_skill_mode("fault_code_explain", flags=flags) == "v2"


def test_retired_shadow_or_invalid_modes_resolve_to_v2(monkeypatch) -> None:
    monkeypatch.setattr(config, "AGENT_ENGINE_VERSION", "v2_shadow")
    assert load_agent_engine_flags().engine_mode == "v2"

    monkeypatch.setattr(config, "AGENT_ENGINE_VERSION", "unknown")
    assert load_agent_engine_flags().engine_mode == "v2"
