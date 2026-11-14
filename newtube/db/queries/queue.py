"""The play queue: what plays next when the current video ends. Local only."""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from .events import log_event
from .seen import SEEN_SQL


def items(conn: sqlite3.Connection, limit: int = 100) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT q.video_id AS id, COALESCE(v.title, '') AS title, v.channel_id, COALESCE(c.title, '') AS channel_title,"
        f" v.published_at, v.duration, v.view_count, p.position AS resume_position, p.completed AS completed, {SEEN_SQL} AS seen"
        " FROM queue_items q LEFT JOIN videos v ON v.id = q.video_id LEFT JOIN channels c ON c.id = v.channel_id"
        " LEFT JOIN video_progress p ON p.video_id = q.video_id ORDER BY q.position LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["completed"], d["seen"] = bool(d["completed"]), bool(d["seen"])
        out.append(d)
    return out


def count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT count(*) FROM queue_items").fetchone()[0]


def add(conn: sqlite3.Connection, video_id: str, *, front: bool = False, now: int | None = None) -> bool:
    """Queue a video (at the end, or in front of everything with ``front``). A video already queued is not duplicated; asking
    for it to play next moves it to the front. Returns whether anything changed."""
    now = now if now is not None else int(time.time())
    row = conn.execute("SELECT position FROM queue_items WHERE video_id=?", (video_id,)).fetchone()
    if row and not front:
        return False
    if front:
        first = conn.execute("SELECT MIN(position) FROM queue_items").fetchone()[0]
        if row and first == row["position"]:
            return False
        pos = (first if first is not None else 1) - 1
    else:
        pos = (conn.execute("SELECT MAX(position) FROM queue_items").fetchone()[0] or 0) + 1
    conn.execute("INSERT INTO queue_items(video_id, position, added_at) VALUES (?,?,?)"
                 " ON CONFLICT(video_id) DO UPDATE SET position=excluded.position", (video_id, pos, now))
    log_event(conn, "queue_add", video_id=video_id, payload={"front": front}, ts=now)
    return True


def remove(conn: sqlite3.Connection, video_id: str) -> bool:
    cur = conn.execute("DELETE FROM queue_items WHERE video_id=?", (video_id,))
    return bool(cur.rowcount)


def reorder(conn: sqlite3.Connection, video_ids: list[str]) -> None:
    """Put the named videos in this order, in front of any not named (those keep their relative order after them)."""
    rest = [r[0] for r in conn.execute("SELECT video_id FROM queue_items ORDER BY position")]
    wanted = [v for v in dict.fromkeys(video_ids) if v in set(rest)]
    final = wanted + [v for v in rest if v not in set(wanted)]
    conn.execute("BEGIN")
    try:
        for i, vid in enumerate(final):
            conn.execute("UPDATE queue_items SET position=? WHERE video_id=?", (i + 1, vid))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def clear(conn: sqlite3.Connection) -> int:
    return conn.execute("DELETE FROM queue_items").rowcount
