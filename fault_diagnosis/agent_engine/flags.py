"""Feature flags for Agent Engine V2 compare and skill cutover."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

EngineMode = Literal["legacy", "v2_plan", "v2_shadow", "v2"]
SkillMode = Literal["legacy", "shadow", "v2"]

SKILL_NAMES = (
    "fault_code_explain",
    "runtime_status",
    "report_generation",
    "alarm_triage",
    "root_cause",
    "workorder_decision",
)

SKILL_ENV_VARS = {
    "fault_code_explain": "AGENT_ENGINE_V2_SKILL_FAULT_CODE_EXPLAIN",
    "runtime_status": "AGENT_ENGINE_V2_SKILL_RUNTIME_STATUS",
    "report_generation": "AGENT_ENGINE_V2_SKILL_REPORT_GENERATION",
    "alarm_triage": "AGENT_ENGINE_V2_SKILL_ALARM_TRIAGE",
    "root_cause": "AGENT_ENGINE_V2_SKILL_ROOT_CAUSE",
    "workorder_decision": "AGENT_ENGINE_V2_SKILL_WORKORDER_DECISION",
}

_ENGINE_MODES = {"legacy", "v2_plan", "v2_shadow", "v2"}
_SKILL_MODES = {"legacy", "shadow", "v2"}


@dataclass(frozen=True)
class AgentEngineFlags:
    engine_mode: EngineMode
    skill_modes: dict[str, SkillMode]


def load_agent_engine_flags() -> AgentEngineFlags:
    engine_mode = _engine_mode()
    default_skill_mode: SkillMode = "shadow" if engine_mode == "v2_shadow" else "legacy"
    return AgentEngineFlags(
        engine_mode=engine_mode,
        skill_modes={skill: _skill_mode_from_env(skill, default=default_skill_mode) for skill in SKILL_NAMES},
    )


def effective_skill_mode(skill_name: str, *, flags: AgentEngineFlags | None = None) -> SkillMode:
    loaded = flags or load_agent_engine_flags()
    if loaded.engine_mode == "v2_shadow":
        return loaded.skill_modes.get(skill_name, "shadow")
    explicit = loaded.skill_modes.get(skill_name, "legacy")
    if loaded.engine_mode == "v2":
        return "v2" if explicit == "v2" else ("shadow" if explicit == "shadow" else "legacy")
    return "legacy"


def should_build_v2_compare(*, flags: AgentEngineFlags | None = None) -> bool:
    loaded = flags or load_agent_engine_flags()
    return loaded.engine_mode in {"v2_shadow", "v2"}


def compare_log_path() -> str:
    from .. import config

    return config.AGENT_ENGINE_V2_COMPARE_LOG_PATH


def _engine_mode() -> EngineMode:
    from .. import config

    value = str(getattr(config, "AGENT_ENGINE_VERSION", "legacy") or "legacy").strip().lower()
    return value if value in _ENGINE_MODES else "legacy"  # type: ignore[return-value]


def _skill_mode_from_env(skill_name: str, *, default: SkillMode = "legacy") -> SkillMode:
    env_name = SKILL_ENV_VARS.get(skill_name, "")
    value = str(os.getenv(env_name, default) or default).strip().lower()
    return value if value in _SKILL_MODES else default  # type: ignore[return-value]
