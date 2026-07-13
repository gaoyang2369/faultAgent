from __future__ import annotations

import json

import pytest

fastapi = pytest.importorskip("fastapi")
testclient = pytest.importorskip("fastapi.testclient")
FastAPI = fastapi.FastAPI
TestClient = testclient.TestClient

from fault_diagnosis.server.agent_gateway.sse_adapter import adapt_sse_chunk
from fault_diagnosis.server.auth.session_scope import SessionScopeManager
from fault_diagnosis.server.devtools.dev_mode import init_dev_state
from fault_diagnosis.server.http.routers import chat as chat_api
from fault_diagnosis.server.http.routers.chat import router as chat_router
from fault_diagnosis.server.http.routers.meta import router as meta_router


def _build_app() -> FastAPI:
    app = FastAPI()
    app.state.session_scope_manager = SessionScopeManager("model-catalog-test-secret")
    init_dev_state(app)
    app.include_router(meta_router)
    app.include_router(chat_router)
    return app


def test_model_catalog_exposes_allowlist_without_credentials(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_NAME", "deepseek-ai/DeepSeek-V3.2")
    monkeypatch.setenv(
        "AVAILABLE_MODEL_NAMES",
        "deepseek-ai/DeepSeek-V3.2,zai-org/GLM-5.2",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")

    with TestClient(_build_app()) as client:
        response = client.get("/api/models")

    assert response.status_code == 200
    assert response.json()["default_model"] == "deepseek-ai/DeepSeek-V3.2"
    assert [item["id"] for item in response.json()["models"]] == [
        "deepseek-ai/DeepSeek-V3.2",
        "zai-org/GLM-5.2",
    ]
    assert "must-not-leak" not in response.text


def test_stream_model_is_validated_and_forwarded(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_NAME", "deepseek-ai/DeepSeek-V3.2")
    monkeypatch.setenv(
        "AVAILABLE_MODEL_NAMES",
        "deepseek-ai/DeepSeek-V3.2,zai-org/GLM-5.2",
    )
    calls: list[str | None] = []

    async def fake_stream_events(app, message, thread_id, user_identity, **kwargs):
        calls.append(kwargs.get("model_name"))
        payload = {"type": "chat_complete", "thread_id": thread_id, "final_content": "ok"}
        yield adapt_sse_chunk(
            "event: complete\ndata: " + json.dumps(payload) + "\n\n",
            None,
            thread_id=thread_id,
            complete_payload_enricher=kwargs.get("complete_payload_enricher"),
        )

    monkeypatch.setattr(chat_api, "token_stream_events", fake_stream_events)

    with TestClient(_build_app()) as client:
        valid_response = client.get(
            "/chat/stream",
            params={"message": "查询故障", "model": "zai-org/GLM-5.2"},
        )
        invalid_response = client.get(
            "/chat/stream",
            params={"message": "查询故障", "model": "unknown/model"},
        )

    assert valid_response.status_code == 200
    assert calls == ["zai-org/GLM-5.2"]
    assert invalid_response.status_code == 400
    assert invalid_response.json()["detail"] == "不支持的模型，请刷新模型列表后重试"
