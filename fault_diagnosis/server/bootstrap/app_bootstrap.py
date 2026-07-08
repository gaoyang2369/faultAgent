from __future__ import annotations

import os

from dotenv import load_dotenv

from fault_diagnosis.shared.encoding import ensure_utf8_stdio
from .server_runner import configure_windows_event_loop_policy
from fault_diagnosis.platform.paths import PROJECT_ENV_FILE


def bootstrap_app_runtime() -> None:
    ensure_utf8_stdio()
    configure_windows_event_loop_policy()
    load_dotenv(dotenv_path=PROJECT_ENV_FILE, override=False)
    os.environ.setdefault("PYTHONUTF8", "1")
