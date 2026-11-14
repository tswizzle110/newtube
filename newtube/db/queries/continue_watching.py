"""Continue watching: the videos you started in newtube and have not finished, most recently watched first.

A video counts as started once you are ``MIN_SECONDS`` in (a stray click does not make a card) and until it is finished
(``video_progress.completed``, set when you watch about nine tenths of it, or reach the end). Removing a video from History
forgets where you stopped, so it leaves this list too. "Mark as watched" finishes it without watching the rest.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

MIN_SECONDS = 10


def started(conn: sqlite3.Connection, limit: int = 60) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT v.id, v.title, v.channel_id, COALESCE(c.title, '') AS channel_title, v.published_at, v.duration AS vduration,"
        " v.view_count, p.position AS resume_position, p.duration AS pduration, p.updated_at AS watched_at"
        " FROM video_progress p JOIN videos v ON v.id = p.video_id LEFT JOIN channels c ON c.id = v.channel_id"
        " WHERE p.completed = 0 AND p.position >= ? AND v.title <> ''"
        " ORDER BY p.updated_at DESC, v.id LIMIT ?", (MIN_SECONDS, limit)).fetchall()
    return [{"id": r["id"], "title": r["title"], "channel_id": r["channel_id"], "channel_title": r["channel_title"],
             "published_at": r["published_at"], "duration": r["vduration"] or r["pduration"], "view_count": r["view_count"],
             "resume_position": r["resume_position"], "completed": False, "seen": True, "watched_at": r["watched_at"]}
            for r in rows]


def mark_watched(conn: sqlite3.Connection, video_id: str, now: int | None = None) -> bool:
    cur = conn.execute("UPDATE video_progress SET completed=1, updated_at=? WHERE video_id=?",
                       (now if now is not None else int(time.time()), video_id))
    return bool(cur.rowcount)
