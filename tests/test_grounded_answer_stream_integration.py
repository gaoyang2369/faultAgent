from __future__ import annotations

import asyncio
import inspect
import json
from types import SimpleNamespace

from fastapi import FastAPI

from fault_diagnosis.agent.contracts import CompositeOutputFrame, DeliverableResult, OutputFrame
from fault_diagnosis.agent.output import GroundedAnswerSynthesizer
from fault_diagnosis.domain.diagnosis.contracts import Claim, EvidenceBundle, EvidenceItem
from fault_diagnosis.domain.security.permissions import build_auth_context
from fault_diagnosis.platform import settings
from fault_diagnosis.platform.observability import TraceRecorder
from fault_diagnosis.server.agent_gateway.answer_synthesis import synthesize_v2_answer
from fault_diagnosis.server.bootstrap import app_models
from fault_diagnosis.server.use_cases.chat_service import ChatService


class _Model:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        return SimpleNamespace(content=self.content)


def _frame() -> tuple[OutputFrame, EvidenceBundle]:
    deliverable = DeliverableResult(
        goal_id="goal_status",
        capability="check_runtime_status",
        status="completed",
        title="运行状态",
        claim_ids=["claim_status"],
        evidence_ids=["ev_status"],
        structured_content={
            "assessments": [
                {
                    "device": "G120电机1",
                    "runtime_status": "attention",
                    "sample_count": 1,
                    "data_basis": {"resolution_mode": "realtime_window"},
                }
            ]
        },
    )
    frame = OutputFrame(
        answer_variant="runtime_status_answer",
        final_answer="模板状态回答",
        composite_output=CompositeOutputFrame(
            deliverables=[deliverable],
            overall_status="completed",
            content="模板状态回答",
            answer_variant="runtime_status_answer",
            legacy_answer_variant="runtime_status_answer",
        ),
    )
    bundle = EvidenceBundle(
        bundle_id="bundle",
        trace_id="trace",
        evidence_items=[
            EvidenceItem(evidence_id="ev_status", source_type="sql", asset_id="G120电机1", summary="G120电机1状态需关注。")
        ],
        claims=[
            Claim(
                claim_id="claim_status",
                claim_type="runtime_status_assessment",
                asset_id="G120电机1",
                statement="G120电机1状态需关注。",
                supporting_evidence_ids=["ev_status"],
                status="final",
            )
        ],
        final_claim_ids=["claim_status"],
    )
    return frame, bundle


def test_stream_boundary_enforces_disabled_flag_before_injected_service(monkeypatch) -> None:
    frame, bundle = _frame()
    app = FastAPI()

    class _MustNotRun:
        def synthesize(self, **kwargs):
            raise AssertionError("disabled flag must prevent synthesis")

    app.state.grounded_answer_synthesizer = _MustNotRun()
    monkeypatch.setattr(settings, "ENABLE_GROUNDED_ANSWER_SYNTHESIS", False)
    result = asyncio.run(
        synthesize_v2_answer(
            app=app,
            user_message="状态",
            output_frame=frame,
            evidence_bundle=bundle,
            runtime_status="completed",
            auth_context=build_auth_context(role="engineer"),
        )
    )
    assert result.status == "disabled"
    assert result.answer == "模板状态回答"
    assert result.attempted is False


def test_stream_boundary_uses_the_single_injected_synthesizer_when_enabled(monkeypatch) -> None:
    frame, bundle = _frame()
    output = json.dumps(
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
    model = _Model(output)
    app = FastAPI()
    app.state.grounded_answer_synthesizer = GroundedAnswerSynthesizer(model=model, enabled=True)
    monkeypatch.setattr(settings, "ENABLE_GROUNDED_ANSWER_SYNTHESIS", True)
    result = asyncio.run(
        synthesize_v2_answer(
            app=app,
            user_message="状态",
            output_frame=frame,
            evidence_bundle=bundle,
            runtime_status="completed",
            auth_context=build_auth_context(role="engineer"),
        )
    )
    assert result.status == "generated"
    assert model.calls == 1


def test_answer_model_builder_is_low_temperature_timed_and_cached(monkeypatch) -> None:
    calls: list[dict] = []

    class _ChatOpenAI:
        def __init__(self, **kwargs) -> None:
            calls.append(kwargs)

    app_models.build_answer_model.cache_clear()
    monkeypatch.setattr(app_models, "ChatOpenAI", _ChatOpenAI)
    monkeypatch.setenv("MODEL_NAME", "answer-model")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://model.invalid/v1")
    monkeypatch.setattr(settings, "ANSWER_SYNTHESIS_TEMPERATURE", 0.0)
    monkeypatch.setattr(settings, "ANSWER_SYNTHESIS_TIMEOUT_SECONDS", 20.0)
    first = app_models.build_answer_model()
    second = app_models.build_answer_model()
    assert first is second
    assert len(calls) == 1
    assert calls[0]["temperature"] == 0.0
    assert calls[0]["timeout"] == 20.0
    app_models.build_answer_model.cache_clear()


def test_trace_records_safe_answer_synthesis_span_only() -> None:
    recorder = TraceRecorder(trace_id="trace", request_id="request", thread_id="thread")
    recorder.add_answer_synthesis(
        {
            "enabled": True,
            "attempted": True,
            "status": "validation_failed",
            "model_name": "answer-model",
            "duration_ms": 12.3,
            "input_char_count": 100,
            "output_char_count": 50,
            "used_claim_count": 0,
            "used_evidence_count": 0,
            "validation_errors": ["unallowed_url"],
            "fallback_reason": "unallowed_url",
            "api_key": "must-not-appear",
            "source_packet": {"secret": "must-not-appear"},
        }
    )
    trace = recorder.finish().model_dump(mode="json")
    span = next(item for item in trace["spans"] if item["name"] == "answer_synthesis")
    assert span["attributes"]["status"] == "validation_failed"
    assert "api_key" not in span["attributes"]
    assert "source_packet" not in span["attributes"]


def test_chat_stream_edit_and_agent_chat_share_the_same_turn_stream_boundary() -> None:
    for method in (ChatService.stream_chat, ChatService.stream_edit, ChatService.agent_chat):
        assert ".stream_turn(" in inspect.getsource(method)
    assert ".stream_turn(" not in inspect.getsource(ChatService.plan_chat)
