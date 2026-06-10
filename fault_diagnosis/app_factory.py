from __future__ import annotations

from fastapi import FastAPI

from .api.app_routes import include_app_routes
from .common.logger import get_logger
from .infrastructure.app_static import mount_static_assets
from .infrastructure.app_lifespan import app_lifespan
from .infrastructure.app_models import build_chat_model, build_summary_model
from .infrastructure.app_setup import build_session_scope_manager, configure_cors
from .repositories.history_index import get_history_index_repository

_log = get_logger("app")


def create_app() -> FastAPI:
    app = FastAPI(title="LangChain 1.0 Streaming Agent API", lifespan=app_lifespan)
    app.state.session_scope_manager = build_session_scope_manager()
    app.state.history_index_repository = get_history_index_repository()
    app.state.chat_model = build_chat_model()
    app.state.summary_model = build_summary_model()

    configure_cors(app)
    mount_static_assets(app)
    include_app_routes(app)
    return app
