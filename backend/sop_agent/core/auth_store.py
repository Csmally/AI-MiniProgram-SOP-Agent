"""用户与会话归属存储 — users / session_owners 表（裸 SQL，与 orchestrator 同风格）。

会话数据本体在 langgraph 的 checkpoints 表（thread_id = session_id，schema 由
PostgresSaver 管理、不可加列），归属用独立的 session_owners 映射表记录：
- 创建会话时 register_owner 落一行，即完成「会话 → 用户」关联；
- 访问/删除前 is_owned 校验，非本人（含无主旧会话）一律视为不存在；
- 无主旧会话在映射表中无行 → 任何用户列表都不可见（保持隐藏，不迁移）。
"""

import psycopg

from .db import get_pool


class UsernameTakenError(RuntimeError):
    """用户名已存在（注册冲突）。"""


def init_db() -> None:
    """建表（幂等，IF NOT EXISTS）：users + session_owners。main lifespan 启动时调用。"""
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS session_owners (
                    session_id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )


# ──────────────────────────────────────────────
# users
# ──────────────────────────────────────────────

def create_user(username: str, password_hash: str) -> int:
    """创建用户，返回 user_id；用户名重复抛 UsernameTakenError。"""
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "INSERT INTO users (username, password_hash) VALUES (%s, %s) RETURNING id",
                    (username, password_hash),
                )
                row = cur.fetchone()
            except psycopg.errors.UniqueViolation:
                raise UsernameTakenError(f"用户名 {username!r} 已被注册") from None
    return row[0]


def get_user_by_username(username: str) -> dict | None:
    return _get_user("username = %s", (username,))


def get_user_by_id(user_id: int) -> dict | None:
    return _get_user("id = %s", (user_id,))


def _get_user(where: str, params: tuple) -> dict | None:
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT id, username, password_hash FROM users WHERE {where}", params)
            row = cur.fetchone()
    if not row:
        return None
    return {"id": row[0], "username": row[1], "password_hash": row[2]}


# ──────────────────────────────────────────────
# session_owners（会话 → 用户 归属）
# ──────────────────────────────────────────────

def register_owner(session_id: str, user_id: int) -> None:
    """登记会话归属（幂等，重复登记不报错）。"""
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO session_owners (session_id, user_id) VALUES (%s, %s) "
                "ON CONFLICT (session_id) DO NOTHING",
                (session_id, user_id),
            )


def is_owned(session_id: str, user_id: int) -> bool:
    """会话是否属于该用户（无主旧会话对任何人都返回 False）。"""
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM session_owners WHERE session_id = %s AND user_id = %s",
                (session_id, user_id),
            )
            return cur.fetchone() is not None


def remove_owner(session_id: str) -> None:
    """删除会话归属记录（会话删除时调用）。"""
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM session_owners WHERE session_id = %s", (session_id,))
