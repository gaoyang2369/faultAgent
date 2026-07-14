from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path

from fastapi import FastAPI
import pytest

from fault_diagnosis.agent.output import GroundedAnswerResult, GroundedAnswerSynthesizer, project_answer_complete_payload
from fault_diagnosis.agent.output.grounded_answer import ANSWER_SYNTHESIS_SYSTEM_PROMPT
from fault_diagnosis.domain.security.permissions import build_auth_context
from fault_diagnosis.platform import settings
from fault_diagnosis.platform.model_catalog import get_answer_model_config, resolve_answer_model_name
from fault_diagnosis.platform.observability import TraceRecorder
from fault_diagnosis.server.agent_gateway import answer_synthesis
from fault_diagnosis.server.agent_gateway.answer_synthesis import grounded_answer_rollout_enabled, synthesize_v2_answer
from fault_diagnosis.server.bootstrap import app_models
from tests.test_grounded_answer_stream_integration import _Model, _frame


def _valid_output() -> str:
    return json.dumps(
        {
            "schema_version": "grounded_answer.v1",
            "answer": "G120电机1状态需关注。",
            "used_claim_ids": ["claim_status"],
            "used_evidence_ids": ["ev_status"],
            "limitations_disclosed": False,
            "data_basis_disclosed": False,
        },
        ensure_ascii=False,
    )


def _call_boundary(app: FastAPI, *, chat_model: str = "chat-model", thread_id: str = "stable-thread"):
    frame, bundle = _frame()
    return asyncio.run(
        synthesize_v2_answer(
            app=app,
            user_message="状态",
            output_frame=frame,
            evidence_bundle=bundle,
            runtime_status="completed",
            auth_context=build_auth_context(role="engineer"),
            thread_id=thread_id,
            model_name=chat_model,
        )
    )


def test_answer_model_name_is_independent_of_chat_request_model(monkeypatch) -> None:
    fixed_model = _Model(_valid_output())
    monkeypatch.setattr(settings, "ENABLE_GROUNDED_ANSWER_SYNTHESIS", True)
    monkeypatch.setattr(settings, "GROUNDED_ANSWER_ROLLOUT_PERCENT", 100)
    monkeypatch.setattr(answer_synthesis, "get_answer_model_config", lambda: ("fixed-answer-model", "answer_model"))
    monkeypatch.setattr(answer_synthesis, "build_answer_model", lambda name: fixed_model)

    result = _call_boundary(FastAPI(), chat_model="user-selected-chat-model")

    assert result.synthesis_status == "generated"
    assert result.answer_model_name == "fixed-answer-model"
    assert result.answer_model_source == "answer_model"
    assert fixed_model.calls and "user-selected-chat-model" not in str(fixed_model.calls)


def test_answer_model_config_records_explicit_and_default_sources(monkeypatch) -> None:
    monkeypatch.setenv("AVAILABLE_ANSWER_MODEL_NAMES", "answer-fixed,default-fixed")
    monkeypatch.setenv("ANSWER_MODEL_NAME", "answer-fixed")
    monkeypatch.setenv("MODEL_NAME", "default-fixed")
    assert get_answer_model_config() == ("answer-fixed", "answer_model")
    monkeypatch.delenv("ANSWER_MODEL_NAME")
    assert get_answer_model_config() == ("default-fixed", "default_model")


def test_non_whitelisted_answer_model_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("AVAILABLE_ANSWER_MODEL_NAMES", "allowed-answer")
    assert resolve_answer_model_name("allowed-answer") == "allowed-answer"
    with pytest.raises(ValueError, match="answer_model_not_allowed"):
        resolve_answer_model_name("arbitrary-user-model")


def test_disabled_flag_and_zero_rollout_never_construct_or_call_model(monkeypatch) -> None:
    def forbidden():
        raise AssertionError("Answer model must not be constructed")

    monkeypatch.setattr(answer_synthesis, "get_answer_model_config", forbidden)
    monkeypatch.setattr(settings, "ENABLE_GROUNDED_ANSWER_SYNTHESIS", False)
    result = _call_boundary(FastAPI())
    assert result.synthesis_status == "disabled"
    assert result.fallback_reason == "feature_disabled"

    monkeypatch.setattr(settings, "ENABLE_GROUNDED_ANSWER_SYNTHESIS", True)
    monkeypatch.setattr(settings, "GROUNDED_ANSWER_ROLLOUT_PERCENT", 0)
    result = _call_boundary(FastAPI())
    assert result.synthesis_status == "disabled"
    assert result.fallback_reason == "rollout_disabled"


def test_rollout_100_calls_once_and_thread_sampling_is_stable(monkeypatch) -> None:
    model = _Model(_valid_output())
    app = FastAPI()
    app.state.grounded_answer_synthesizer = GroundedAnswerSynthesizer(model=model, enabled=True)
    monkeypatch.setattr(settings, "ENABLE_GROUNDED_ANSWER_SYNTHESIS", True)
    monkeypatch.setattr(settings, "GROUNDED_ANSWER_ROLLOUT_PERCENT", 100)
    assert _call_boundary(app).synthesis_status == "generated"
    assert model.calls == 1

    first = grounded_answer_rollout_enabled(thread_id="same-thread", enabled=True, percent=37)
    assert all(grounded_answer_rollout_enabled(thread_id="same-thread", enabled=True, percent=37) == first for _ in range(20))
    assert grounded_answer_rollout_enabled(thread_id="", enabled=True, percent=37) is False


def test_complete_and_trace_share_phase16_metric_semantics() -> None:
    result = GroundedAnswerResult(
        status="model_error",
        synthesis_status="model_timeout",
        answer="fallback",
        enabled=True,
        attempted=True,
        fallback_reason="model_timeout",
        provider_returned=False,
        schema_valid=False,
        answer_validated=False,
        request_timeout_seconds=8,
        answer_model_name="fixed-answer",
        answer_model_source="answer_model",
    )
    complete = project_answer_complete_payload({}, deterministic_answer="fallback", answer_result=result)["answer_synthesis"]
    recorder = TraceRecorder(trace_id="trace", request_id="request", thread_id="thread")
    recorder.add_answer_synthesis(result.audit_summary())
    trace = recorder.finish().model_dump(mode="json")
    attrs = next(span["attributes"] for span in trace["spans"] if span["name"] == "answer_synthesis")
    for key in (
        "synthesis_status",
        "final_answer_source",
        "fallback_used",
        "answer_model_name",
        "answer_model_source",
        "provider_returned",
        "schema_valid",
        "answer_validated",
        "request_timeout_seconds",
        "fallback_reason",
    ):
        assert attrs[key] == complete[key]


def test_legacy_phase15_failures_count_as_thirteen_real_fallbacks() -> None:
    results = [
        GroundedAnswerResult(
            status="model_error",
            synthesis_status="model_timeout",
            answer="fallback",
            fallback_reason="model_timeout",
        )
        for _ in range(12)
    ]
    results.append(
        GroundedAnswerResult(
            status="validation_failed",
            synthesis_status="validation_failed",
            answer="fallback",
            provider_returned=True,
            schema_valid=True,
            fallback_reason="unallowed_device_reference",
        )
    )
    results.extend(
        GroundedAnswerResult(
            status="generated",
            synthesis_status="generated",
            final_answer_source="grounded_model",
            fallback_used=False,
            answer="generated",
            provider_returned=True,
            schema_valid=True,
            answer_validated=True,
        )
        for _ in range(7)
    )
    assert sum(item.fallback_used for item in results) == 13
    assert sum(item.synthesis_status == "model_timeout" for item in results) == 12
    assert sum(item.synthesis_status == "validation_failed" for item in results) == 1


def test_benchmark_is_not_a_production_dependency_and_diagnostic_timeout_is_isolated() -> None:
    root = Path(__file__).resolve().parents[1]
    production_sources = "\n".join(path.read_text(encoding="utf-8") for path in (root / "fault_diagnosis").rglob("*.py"))
    benchmark_source = (root / "tests/evals/benchmark_grounded_answer_models.py").read_text(encoding="utf-8")
    assert "tests.evals.benchmark_grounded_answer_models" not in production_sources
    assert "--diagnostic-timeout" in benchmark_source
    assert "default=60.0" in benchmark_source
    assert settings.ANSWER_MODEL_REQUEST_TIMEOUT_SECONDS == 8.0


def test_prompt_is_trimmed_but_keeps_all_core_safety_constraints() -> None:
    assert len(ANSWER_SYNTHESIS_SYSTEM_PROMPT) <= 1800
    for required in ("Source Packet", "设备", "故障码", "URL", "工单", "limitations", "固定 JSON", "used_claim_ids", "不可信数据"):
        assert required in ANSWER_SYNTHESIS_SYSTEM_PROMPT


def test_answer_builder_sends_only_provider_neutral_parameters(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    app_models.build_answer_model.cache_clear()
    monkeypatch.setattr(app_models, "ChatOpenAI", FakeChatOpenAI)
    monkeypatch.setenv("AVAILABLE_ANSWER_MODEL_NAMES", "candidate")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    app_models.build_answer_model("candidate", True, 8.0, 512)
    assert set(calls[0]) == {
        "model", "base_url", "api_key", "temperature", "timeout", "max_tokens", "max_retries", "model_kwargs"
    }
    assert not ({"enable_thinking", "reasoning_effort", "thinking_budget"} & set(calls[0]))
    app_models.build_answer_model.cache_clear()


def test_answer_model_cache_remains_bounded() -> None:
    assert app_models.build_answer_model.cache_info().maxsize == 8
    assert inspect.signature(app_models.build_answer_model).parameters["model_name"].default is inspect.Parameter.empty
