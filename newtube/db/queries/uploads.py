"""Stored upload lists: read once in full, then topped up cheaply (see services/lists.py)."""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from ...extract.base import VideoSummary


def scan_info(conn: sqlite3.Connection, channel_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM channel_scans WHERE channel_id=?", (channel_id,)).fetchone()


def count(conn: sqlite3.Connection, channel_id: str) -> int:
    return conn.execute("SELECT count(*) FROM channel_uploads WHERE channel_id=?", (channel_id,)).fetchone()[0]


def store_scan(conn: sqlite3.Connection, channel_id: str, videos: list[VideoSummary], total: int | None,
               truncated: bool, now: int | None = None) -> None:
    """Replace the channel's stored list with a fresh full read (newest first). One transaction, so
    readers never see half of it, and videos that were deleted upstream drop out."""
    now = now if now is not None else int(time.time())
    conn.execute("BEGIN")
    try:
        conn.execute("DELETE FROM channel_uploads WHERE channel_id=?", (channel_id,))
        conn.executemany(
            "INSERT OR REPLACE INTO channel_uploads(channel_id, video_id, pos, title, duration, view_count)"
            " VALUES (?,?,?,?,?,?)",
            [(channel_id, v.id, i, v.title, v.duration, v.view_count) for i, v in enumerate(videos)])
        conn.execute(
            "INSERT INTO channel_scans(channel_id, total, truncated, scanned_at, topped_up_at) VALUES (?,?,?,?,?)"
            " ON CONFLICT(channel_id) DO UPDATE SET total=excluded.total, truncated=excluded.truncated,"
            "  scanned_at=excluded.scanned_at, topped_up_at=excluded.topped_up_at",
            (channel_id, total, int(truncated), now, now))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def top_up(conn: sqlite3.Connection, channel_id: str, newest_first: list[VideoSummary],
           now: int | None = None) -> str:
    """Merge the newest page of uploads into the stored list. Returns 'ok', or 'gap' when NONE of the
    page's videos are known: the channel posted more than a page since we last looked, so something
    may be missing in between and a full rescan is needed."""
    now = now if now is not None else int(time.time())
    ids = [v.id for v in newest_first]
    known: set[str] = set()
    if ids:
        marks = ",".join("?" * len(ids))
        known = {r[0] for r in conn.execute(
            f"SELECT video_id FROM channel_uploads WHERE channel_id=? AND video_id IN ({marks})", (channel_id, *ids))}
        if not known and count(conn, channel_id):
            return "gap"
    new = [v for v in newest_first if v.id not in known]
    lowest = conn.execute("SELECT MIN(pos) FROM channel_uploads WHERE channel_id=?", (channel_id,)).fetchone()[0] or 0
    conn.execute("BEGIN")
    try:
        for i, v in enumerate(new):            # newest of the new ones gets the smallest number
            conn.execute("INSERT INTO channel_uploads(channel_id, video_id, pos, title, duration, view_count)"
                         " VALUES (?,?,?,?,?,?)", (channel_id, v.id, lowest - len(new) + i, v.title, v.duration, v.view_count))
        for v in newest_first:                 # fresher titles and view counts for what we already had
            if v.id in known:
                conn.execute("UPDATE channel_uploads SET title=?, duration=COALESCE(?, duration),"
                             " view_count=COALESCE(?, view_count) WHERE channel_id=? AND video_id=?",
                             (v.title, v.duration, v.view_count, channel_id, v.id))
        conn.execute("UPDATE channel_scans SET topped_up_at=?, total=CASE WHEN total IS NULL THEN NULL ELSE total+? END"
                     " WHERE channel_id=?", (now, len(new), channel_id))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return "ok"


_ORDER = {
    "oldest": "pos DESC",
    "popular": "view_count IS NULL, view_count DESC, pos ASC",      # unknown views last; ties newest first
    "newest": "pos ASC",
}


def page(conn: sqlite3.Connection, channel_id: str, sort: str, offset: int, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"SELECT video_id, title, duration, view_count FROM channel_uploads WHERE channel_id=?"
        f" ORDER BY {_ORDER[sort]} LIMIT ? OFFSET ?", (channel_id, limit, offset))
    return [{"kind": "video", "id": r["video_id"], "title": r["title"], "channel_id": channel_id, "channel_title": None,
             "duration": r["duration"], "view_count": r["view_count"], "published_at": None, "is_live": False}
            for r in rows]
