from __future__ import annotations

import importlib.util

import pytest

from fault_diagnosis.agent import AgentEngineV2


def test_retired_plan_compare_and_diff_modules_are_absent() -> None:
    assert importlib.util.find_spec("fault_diagnosis.agent.observability.compare") is None
    assert importlib.util.find_spec("fault_diagnosis.agent.planning.plan_diff") is None


@pytest.mark.parametrize("keyword", ["legacy_plan", "llm_candidate_plan", "recent_context_signals"])
def test_engine_rejects_retired_authority_inputs(keyword: str) -> None:
    with pytest.raises(TypeError):
        AgentEngineV2().build_plan_snapshot(raw_message="查询 G120电机1 状态", **{keyword: {}})
