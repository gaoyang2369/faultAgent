from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..auth.session_scope import SessionScopeManager
from ..config import FRONTEND_ORIGINS, SESSION_SECRET, SESSION_SECRET_SOURCE


def build_session_scope_manager() -> SessionScopeManager:
    return SessionScopeManager(
        SESSION_SECRET or None,
        secret_source=SESSION_SECRET_SOURCE if SESSION_SECRET else None,
    )


def configure_cors(app: FastAPI) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=FRONTEND_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
