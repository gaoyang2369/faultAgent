"""全局异步 MySQL 连接池 -- 配合 FastAPI lifespan 管理生命周期。

生命周期：
    startup:  await init_pool()
    runtime:  async with get_pool().acquire() as conn: ...
    shutdown: await close_pool()
"""
import os
import aiomysql
from dotenv import load_dotenv
from ..config import MYSQL_USER
from ..common.logger import get_logger

_log = get_logger("db_pool")


_pool: aiomysql.Pool | None = None


def _mask_host(host: str | None) -> str:
    if not host:
        return ""
    parts = host.split(".")
    if len(parts) == 4:
        return ".".join(parts[:3] + ["xxx"])
    return host[:24] + ("..." if len(host) > 24 else "")


async def init_pool() -> aiomysql.Pool:
    """创建全局 MySQL 异步连接池（在 FastAPI lifespan startup 中调用）。"""
    global _pool
    if _pool is not None:
        return _pool

    load_dotenv(override=False)
    host = os.getenv("HOST", "localhost")
    db_name = os.getenv("DB_NAME", "")
    _pool = await aiomysql.create_pool(
        host=host,
        user=MYSQL_USER,
        password=os.getenv("MYSQL_PW", ""),
        db=db_name,
        port=int(os.getenv("PORT", "3306")),
        charset="utf8mb4",
        minsize=2,
        maxsize=10,
        autocommit=True,
    )
    _log.info("MySQL 异步连接池初始化成功", host=_mask_host(host), db=db_name)
    return _pool


async def close_pool() -> None:
    """关闭连接池（在 FastAPI lifespan shutdown 中调用）。"""
    global _pool
    if _pool is not None:
        _pool.close()
        await _pool.wait_closed()
        _pool = None
        _log.info("MySQL 异步连接池已关闭")


def get_pool() -> aiomysql.Pool:
    """获取当前连接池实例（运行时调用）。

    Raises:
        RuntimeError: 连接池未初始化时抛出。
    """
    if _pool is None:
        raise RuntimeError(
            "MySQL 连接池未初始化，请确保 FastAPI lifespan 已正确启动"
        )
    return _pool
