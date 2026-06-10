from __future__ import annotations

import os

from fastapi import FastAPI
from starlette.staticfiles import StaticFiles

from ..common.logger import get_logger
from ..common.paths import FRONTEND_PUBLIC_DIR

_log = get_logger("app.static")


def mount_static_assets(app: FastAPI) -> None:
    static_dir = FRONTEND_PUBLIC_DIR
    if not os.path.exists(static_dir):
        _log.warning("Static asset directory not found", path=static_dir)
        return

    images_dir = os.path.join(static_dir, "images")
    reports_dir = os.path.join(static_dir, "reports")
    os.makedirs(images_dir, exist_ok=True)
    os.makedirs(reports_dir, exist_ok=True)

    app.mount("/images", StaticFiles(directory=images_dir), name="images")
    app.mount("/reports", StaticFiles(directory=reports_dir), name="reports")
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    _log.info("静态文件已挂载", path=static_dir)
