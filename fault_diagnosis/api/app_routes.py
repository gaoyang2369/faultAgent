from __future__ import annotations

from fastapi import FastAPI

from .admin_pdfs import router as admin_pdfs_router
from .auth import router as auth_router
from .chat import router as chat_router
from .governance import router as governance_router
from .health import router as health_router
from .history import router as history_router
from .meta import router as meta_router
from .tts import router as tts_router
from .workorders import router as workorders_router


def include_app_routes(app: FastAPI) -> None:
    app.include_router(meta_router)
    app.include_router(auth_router)
    app.include_router(admin_pdfs_router)
    app.include_router(chat_router)
    app.include_router(tts_router)
    app.include_router(health_router)
    app.include_router(history_router)
    app.include_router(governance_router)
    app.include_router(workorders_router)
