from __future__ import annotations

import importlib
import os

import fault_diagnosis.config as config


def _read_agent_engine_config(value: str | None) -> dict[str, object]:
    original = os.environ.get("AGENT_ENGINE_VERSION")
    try:
        if value is None:
            os.environ.pop("AGENT_ENGINE_VERSION", None)
        else:
            os.environ["AGENT_ENGINE_VERSION"] = value
        loaded = importlib.reload(config)
        return {
            "default": loaded.AGENT_ENGINE_VERSION_DEFAULT,
            "choices": set(loaded.AGENT_ENGINE_VERSION_CHOICES),
            "version": loaded.AGENT_ENGINE_VERSION,
        }
    finally:
        if original is None:
            os.environ.pop("AGENT_ENGINE_VERSION", None)
        else:
            os.environ["AGENT_ENGINE_VERSION"] = original
        importlib.reload(config)


def test_agent_engine_version_defaults_to_v2() -> None:
    loaded = _read_agent_engine_config(None)

    assert loaded["default"] == "v2"
    assert loaded["version"] == "v2"


def test_agent_engine_version_accepts_declared_modes() -> None:
    loaded = _read_agent_engine_config("v2")

    assert loaded["version"] == "v2"
    assert loaded["choices"] == {"v2"}


def test_agent_engine_version_retired_legacy_value_falls_back_to_v2() -> None:
    loaded = _read_agent_engine_config("legacy")

    assert loaded["version"] == "v2"


def test_agent_engine_version_invalid_value_falls_back_to_v2() -> None:
    loaded = _read_agent_engine_config("v2_execute_now")

    assert loaded["version"] == "v2"
