from types import SimpleNamespace

from fault_diagnosis.single_agent.planning import (
    build_manual_confirmation_requirement,
    contains_forbidden_execution_phrase,
)


def _decision(**overrides):
    data = {
        "primary_task_type": "action_request",
        "task_family": "action_or_workorder",
        "action_type": None,
        "action_target": None,
        "user_goal": "生成工单草稿",
        "intent_stack": ["create_workorder_draft"],
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_workorder_action_requires_human_confirmation() -> None:
    contract = build_manual_confirmation_requirement(
        decision=_decision(),
        workorder_action_readiness={"action_type": "workorder_draft", "stale_refresh_required": False, "blockers": []},
    )

    assert contract.required is True
    assert contract.confirmation_type == "workorder_draft"
    assert contract.required_role == "engineer"
    assert contract.allowed_next_step == "draft_only"


def test_workorder_decision_can_only_ask_confirmation() -> None:
    contract = build_manual_confirmation_requirement(
        decision=_decision(user_goal="是否要派单"),
        workorder_action_readiness={"action_type": "workorder_decision", "stale_refresh_required": False, "blockers": []},
    )

    assert contract.required is True
    assert contract.confirmation_type == "workorder_draft"
    assert contract.allowed_next_step == "ask_confirmation"


def test_stale_workorder_requires_refresh_first() -> None:
    contract = build_manual_confirmation_requirement(
        decision=_decision(),
        workorder_action_readiness={
            "action_type": "workorder_decision",
            "stale_refresh_required": True,
            "blockers": ["stale_refresh_or_disclosure_required"],
        },
    )

    assert contract.allowed_next_step == "refresh_data_first"


def test_device_reset_and_stop_are_denied_for_execution() -> None:
    reset = build_manual_confirmation_requirement(
        decision=_decision(action_type="reset", user_goal="复位设备"),
        workorder_action_readiness={"action_type": "device_action", "stale_refresh_required": False, "blockers": []},
    )
    stop = build_manual_confirmation_requirement(
        decision=_decision(action_type="stop", user_goal="停机"),
        workorder_action_readiness={"action_type": "device_action", "stale_refresh_required": False, "blockers": []},
    )

    assert reset.confirmation_type == "reset"
    assert reset.required_role == "admin"
    assert reset.allowed_next_step == "deny"
    assert stop.confirmation_type == "stop_machine"
    assert stop.allowed_next_step == "deny"


def test_parameter_change_is_denied_and_forbidden_phrases_are_detected() -> None:
    contract = build_manual_confirmation_requirement(
        decision=_decision(action_type="parameter_change", user_goal="修改参数"),
        workorder_action_readiness={"action_type": "device_action", "stale_refresh_required": False, "blockers": []},
    )

    assert contract.confirmation_type == "parameter_change"
    assert contract.allowed_next_step == "deny"
    assert contains_forbidden_execution_phrase("已修改参数，请查看结果") is True
    assert contains_forbidden_execution_phrase("可以生成待确认草稿") is False
