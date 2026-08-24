"""Postgres 连接池 — 图执行与数据访问共享的 psycopg 连接池。

从 orchestrator 抽出：auth_store 等非图模块也要访问同一数据库，
避免各建各的池。线程安全（SSE worker 线程共享）。
"""

from psycopg_pool import ConnectionPool

from .config import get_settings

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    """Postgres 连接池（懒加载单例，进程内共享）。"""
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=get_settings().DATABASE_URL,
            min_size=1,
            max_size=5,
            kwargs={"autocommit": True, "prepare_threshold": 0},
        )
    return _pool


def close_pool() -> None:
    """关闭连接池（FastAPI lifespan 退出时调用）。

    显式超时：等待连接归还最多 5s，避免 reload/关机时因 worker 线程
    未结束而无限挂起（进程假死、端口被占）。
    """
    global _pool
    if _pool is not None:
        try:
            _pool.close(timeout=5)
        except Exception:
            pass  # 强制关闭场景下连接可能无法归还，忽略并退出
        _pool = None
