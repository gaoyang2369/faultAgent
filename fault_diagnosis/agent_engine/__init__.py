"""Agent Engine V2 sidecar package."""

from .contracts import (
    EvidenceLedger,
    ExecutionPlan,
    IntentFrame,
    NodeResult,
    OutputFrame,
    PlanSnapshotV2,
    RewriteFrame,
    ContextFrame,
    SkillRoute,
)
from .context import ContextFrameAdapter
from .engine import AgentEngineV2
from .planning import PlanCompiler, PlanPolicyBridge, PlanValidationResult, PlanValidator, diff_plans
from .skills import LoadedSkill, SkillLoader, SkillMetadata, SkillRegistry, SkillRouter
from .understanding import IntentFrameBuilder, RewriteFrameBuilder

__all__ = [
    "AgentEngineV2",
    "ContextFrame",
    "ContextFrameAdapter",
    "EvidenceLedger",
    "ExecutionPlan",
    "IntentFrame",
    "IntentFrameBuilder",
    "LoadedSkill",
    "NodeResult",
    "OutputFrame",
    "PlanCompiler",
    "PlanPolicyBridge",
    "PlanSnapshotV2",
    "PlanValidationResult",
    "PlanValidator",
    "RewriteFrame",
    "RewriteFrameBuilder",
    "SkillLoader",
    "SkillMetadata",
    "SkillRegistry",
    "SkillRoute",
    "SkillRouter",
    "diff_plans",
]
