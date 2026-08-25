"""trace_runs 存储 — 建表 + 事件写入 + 查询（裸 SQL，共享 db 连接池）。

采集侧（本仓库）只写；展示侧（独立 TracePlatform 项目）直连同一库读取。
"""

import json
import uuid
from typing import Any

from ..core.db import get_pool

INPUT_OUTPUT_CAP = 16_000   # input/output 单字段字符上限（PRD 全文 prompt 很长）


def init_db() -> None:
    """建表（幂等）：trace_runs + 索引。main lifespan 启动时调用。"""
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS trace_runs (
                    id UUID PRIMARY KEY,
                    parent_id UUID NULL,
                    session_id TEXT NOT NULL,
                    user_id INT NULL,
                    kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    node TEXT NULL,
                    input JSONB,
                    output JSONB,
                    tokens_in INT,
                    tokens_out INT,
                    duration_ms INT,
                    error TEXT,
                    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    ended_at TIMESTAMPTZ NULL
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_trace_runs_session "
                "ON trace_runs(session_id, started_at)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_trace_runs_parent ON trace_runs(parent_id)"
            )


def _safe_json(obj: Any, max_chars: int = INPUT_OUTPUT_CAP) -> Any:
    """任意对象转 JSON 安全值；超长字符串截断加标记。

    返回 dict/list/str/int/float/None（可直接存 JSONB）；
    无法序列化的对象退化为 repr 字符串。
    """
    try:
        text = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        text = str(obj)
    if len(text) > max_chars:
        # 截断时保留尾巴（错误信息常在尾部）并标记
        text = "[已截断] " + text[:max_chars] + " …"
    try:
        return json.loads(text)
    except Exception:
        return text


def insert_run(run_id: uuid.UUID, parent_id: uuid.UUID | None, session_id: str,
               user_id: int | None, kind: str, name: str, node: str | None,
               input_obj: Any) -> None:
    """run 开始事件落库（started_at 由 DB 默认 now()）。"""
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO trace_runs "
                "(id, parent_id, session_id, user_id, kind, name, node, input) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (run_id, parent_id, session_id, user_id, kind, name, node,
                 json.dumps(_safe_json(input_obj), ensure_ascii=False)),
            )


def update_run(run_id: uuid.UUID, output_obj: Any | None = None,
               tokens_in: int | None = None, tokens_out: int | None = None,
               error: str | None = None) -> None:
    """run 结束事件回填（output/tokens/耗时/错误）。"""
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE trace_runs SET
                    output = COALESCE(%s, output),
                    tokens_in = COALESCE(%s, tokens_in),
                    tokens_out = COALESCE(%s, tokens_out),
                    error = COALESCE(%s, error),
                    ended_at = now(),
                    duration_ms = EXTRACT(EPOCH FROM (now() - started_at)) * 1000
                WHERE id = %s
                """,
                (
                    json.dumps(_safe_json(output_obj), ensure_ascii=False) if output_obj is not None else None,
                    tokens_in,
                    tokens_out,
                    error,
                    run_id,
                ),
            )


def delete_session_traces(session_id: str) -> None:
    """删会话时级联清掉其全部 trace。"""
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM trace_runs WHERE session_id = %s", (session_id,))
