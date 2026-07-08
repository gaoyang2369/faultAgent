"""Feature flags for the Agent Engine V2 default runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

EngineMode = Literal["legacy", "v2"]
SkillMode = Literal["v2"]

SKILL_NAMES = (
    "fault_code_explain",
    "runtime_status",
    "report_generation",
    "alarm_triage",
    "root_cause",
    "workorder_decision",
)


@dataclass(frozen=True)
class AgentEngineFlags:
    engine_mode: EngineMode
    skill_modes: dict[str, SkillMode] = field(default_factory=dict)


def load_agent_engine_flags() -> AgentEngineFlags:
    return AgentEngineFlags(engine_mode=_engine_mode(), skill_modes={skill: "v2" for skill in SKILL_NAMES})


def is_legacy_rollback_enabled(*, flags: AgentEngineFlags | None = None) -> bool:
    loaded = flags or load_agent_engine_flags()
    return loaded.engine_mode == "legacy"


def effective_skill_mode(skill_name: str, *, flags: AgentEngineFlags | None = None) -> SkillMode:  # noqa: ARG001
    return "v2"


def should_build_v2_compare(*, flags: AgentEngineFlags | None = None) -> bool:  # noqa: ARG001
    return False


def compare_log_path() -> str:
    from .. import config

    return config.AGENT_ENGINE_V2_COMPARE_LOG_PATH


def _engine_mode() -> EngineMode:
    from .. import config

    value = str(getattr(config, "AGENT_ENGINE_VERSION", "v2") or "v2").strip().lower()
    return "legacy" if value == "legacy" else "v2"
