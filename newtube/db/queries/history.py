"""Your watch history: imported Takeout watches plus what you watched in newtube, one row per video."""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from .events import log_event
from .seen import not_hidden

_HISTORY = f"""
WITH h AS (
    SELECT e.video_id AS video_id, e.ts AS ts FROM events e
     WHERE e.type = 'import_watch' AND e.video_id IS NOT NULL AND {not_hidden('e.video_id', 'e.ts')}
    UNION ALL
    SELECT w.video_id, w.started_at FROM watch_sessions w WHERE {not_hidden('w.video_id', 'w.started_at')}
)
SELECT h.video_id AS id, max(h.ts) AS watched_at, COALESCE(v.title, '') AS title, v.channel_id,
       COALESCE(c.title, '') AS channel_title, v.published_at, v.duration, v.view_count,
       p.position AS resume_position, p.completed AS completed
FROM h
LEFT JOIN videos v ON v.id = h.video_id
LEFT JOIN channels c ON c.id = v.channel_id
LEFT JOIN video_progress p ON p.video_id = h.video_id
{{where}}
GROUP BY h.video_id
ORDER BY watched_at DESC, h.video_id
LIMIT ? OFFSET ?
"""


def page(conn: sqlite3.Connection, limit: int = 60, offset: int = 0, q: str = "") -> list[dict[str, Any]]:
    """Newest watch first. ``q`` filters by title or channel (case-insensitive)."""
    args: list[Any] = []
    where = ""
    if q.strip():
        where = "WHERE (v.title LIKE ? ESCAPE '\\' OR c.title LIKE ? ESCAPE '\\')"
        like = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        args += [like, like]
    rows = conn.execute(_HISTORY.format(where=where), (*args, limit, offset)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["completed"], d["seen"] = bool(d["completed"]), True
        out.append(d)
    return out


def remove(conn: sqlite3.Connection, video_id: str, now: int | None = None) -> None:
    """Forget that you watched this video (up to now), including where you stopped."""
    now = now if now is not None else int(time.time())
    conn.execute("INSERT INTO history_hidden(video_id, hidden_at) VALUES (?,?)"
                 " ON CONFLICT(video_id) DO UPDATE SET hidden_at=excluded.hidden_at", (video_id, now))
    conn.execute("DELETE FROM video_progress WHERE video_id=?", (video_id,))
    log_event(conn, "history_remove", video_id=video_id, ts=now)
