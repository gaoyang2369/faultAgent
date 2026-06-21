from __future__ import annotations

import os

from fastapi import FastAPI
from starlette.staticfiles import StaticFiles

from ..common.logger import get_logger
from ..common.paths import FRONTEND_PUBLIC_DIR

_log = get_logger("app.static")


class FrontendStaticFiles(StaticFiles):
    """Do not expose legacy report files through the generic static mount."""

    async def get_response(self, path: str, scope):
        normalized = path.replace("\\", "/").lstrip("/")
        if normalized == "reports" or normalized.startswith("reports/"):
            from starlette.responses import Response

            return Response(status_code=404)
        return await super().get_response(path, scope)


def mount_static_assets(app: FastAPI) -> None:
    static_dir = FRONTEND_PUBLIC_DIR
    if not os.path.exists(static_dir):
        _log.warning("Static asset directory not found", path=static_dir)
        return

    images_dir = os.path.join(static_dir, "images")
    os.makedirs(images_dir, exist_ok=True)

    app.mount("/images", StaticFiles(directory=images_dir), name="images")
    app.mount("/static", FrontendStaticFiles(directory=static_dir), name="static")
    _log.info("静态文件已挂载", path=static_dir)
