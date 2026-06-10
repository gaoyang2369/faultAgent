from .app_factory import create_app
from .common.logger import get_logger
from .infrastructure.app_bootstrap import bootstrap_app_runtime
from .infrastructure.server_runner import run_backend_server

_log = get_logger("app")

bootstrap_app_runtime()
app = create_app()


def main() -> None:
    _log.info("启动 LangChain 1.0 流式聊天服务", sse_endpoint="http://localhost:8000/chat/stream")
    run_backend_server(
        app,
        app_import_path="fault_diagnosis.app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_excludes=["**/*.pyc", "**/__pycache__/*"],
    )


if __name__ == "__main__":
    main()
