"""Feature flags for the Agent Engine V2 runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

EngineMode = Literal["v2"]
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


def effective_skill_mode(skill_name: str, *, flags: AgentEngineFlags | None = None) -> SkillMode:  # noqa: ARG001
    return "v2"


def _engine_mode() -> EngineMode:
    return "v2"
