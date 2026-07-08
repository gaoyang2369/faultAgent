"""会话级命名空间存储 -- 替代 globals() 的并发安全方案。

使用 contextvars.ContextVar 为每个异步任务/线程提供独立的命名空间字典，
防止多用户并发时 DataFrame 数据串扰。

使用方式：
    # extract_data 中存入
    get_namespace()[df_name] = df

    # fig_inter 中读取（作为 exec 的 globals 参数）
    exec(py_code, get_namespace(), local_vars)
"""

import contextvars
from typing import Any, Dict


# 每个异步任务/线程拥有独立的命名空间字典
_session_ns: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar(
    "session_namespace", default=None
)


def get_namespace() -> Dict[str, Any]:
    """获取当前会话的命名空间，不存在则自动创建。

    返回的字典中使用空 __builtins__ 沙箱，限制 exec() 执行时可访问的内建函数。
    """

    ns = _session_ns.get(None)
    if ns is None:
        ns = {"__builtins__": {}}
        _session_ns.set(ns)
    return ns


def set_namespace(ns: Dict[str, Any]) -> None:
    """显式设置当前会话的命名空间（用于请求入口处初始化）。"""

    current = _session_ns.get(None)
    merged: Dict[str, Any] = {"__builtins__": ns.get("__builtins__", {})}
    if isinstance(current, dict):
        for key, value in current.items():
            if key != "__builtins__" and key not in ns:
                merged[key] = value
    for key, value in ns.items():
        if key != "__builtins__":
            merged[key] = value
    _session_ns.set(merged)


def clear_namespace() -> None:
    """清理当前会话的命名空间（请求结束时调用）。"""

    _session_ns.set(None)
