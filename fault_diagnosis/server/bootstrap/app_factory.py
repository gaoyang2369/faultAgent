from __future__ import annotations

from fastapi import FastAPI

from fault_diagnosis.platform.logging import get_logger
from fault_diagnosis.platform.persistence.repositories.history_index import get_history_index_repository
from fault_diagnosis.server.bootstrap.app_lifespan import app_lifespan
from fault_diagnosis.server.bootstrap.app_models import build_chat_model, build_summary_model
from fault_diagnosis.server.bootstrap.app_setup import build_session_scope_manager, configure_cors, configure_request_logging
from fault_diagnosis.server.bootstrap.app_static import mount_static_assets
from fault_diagnosis.server.http.routers.app_routes import include_app_routes

_log = get_logger("app")


def create_app() -> FastAPI:
    app = FastAPI(title="LangChain 1.0 Streaming Agent API", lifespan=app_lifespan)
    app.state.session_scope_manager = build_session_scope_manager()
    app.state.history_index_repository = get_history_index_repository()
    app.state.chat_model = build_chat_model()
    app.state.summary_model = build_summary_model()

    configure_cors(app)
    configure_request_logging(app)
    mount_static_assets(app)
    include_app_routes(app)
    return app
