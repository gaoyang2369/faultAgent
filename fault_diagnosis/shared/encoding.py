"""运行时字符编码初始化工具。"""

from __future__ import annotations

import os
import sys
from typing import TextIO


_CONFIGURED = False


def _reconfigure_text_stream(stream: TextIO | None) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if not callable(reconfigure):
        return
    try:
        reconfigure(encoding="utf-8")
    except (AttributeError, OSError, ValueError):
        # pytest、IDE、gunicorn 或日志捕获器可能包装 stdout/stderr。
        # 编码初始化失败不应阻断服务启动。
        return


def ensure_utf8_stdio() -> None:
    """尽量将 Python 标准输入输出链路固定为 UTF-8。

    函数设计为幂等：环境变量使用 setdefault，stdout/stderr 只在进程内尝试
    reconfigure 一次；如果流对象不支持 reconfigure 或当前环境禁止修改，静默降级。
    """

    global _CONFIGURED

    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")

    if _CONFIGURED:
        return

    _reconfigure_text_stream(getattr(sys, "stdout", None))
    _reconfigure_text_stream(getattr(sys, "stderr", None))
    _CONFIGURED = True
