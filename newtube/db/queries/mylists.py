"""Your own lists: Liked videos, Watch later, and the playlists you make. One data model for all of them.

Every change also lands in the event log (like / unlike / watch_later_add / watch_later_remove / playlist_add /
playlist_remove), which is what the recommender learns from over time."""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from .events import log_event
from .seen import SEEN_SQL

SYSTEM_EVENTS = {"liked": ("like", "unlike"), "watch_later": ("watch_later_add", "watch_later_remove")}


class NoSuchList(LookupError):
    pass


def resolve(conn: sqlite3.Connection, ref: str) -> sqlite3.Row:
    """``ref`` is 'liked', 'watch_later' or a list's numeric id."""
    if ref in SYSTEM_EVENTS:
        row = conn.execute("SELECT * FROM playlists WHERE kind=?", (ref,)).fetchone()
    elif ref.isdigit():
        row = conn.execute("SELECT * FROM playlists WHERE id=?", (int(ref),)).fetchone()
    else:
        row = None
    if row is None:
        raise NoSuchList(ref)
    return row


def list_all(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT p.id, p.kind, p.name, (SELECT count(*) FROM playlist_items i WHERE i.playlist_id = p.id) AS count"
        " FROM playlists p ORDER BY CASE p.kind WHEN 'liked' THEN 0 WHEN 'watch_later' THEN 1 ELSE 2 END,"
        " lower(p.name), p.id").fetchall()
    return [dict(r) for r in rows]


def items(conn: sqlite3.Connection, playlist: sqlite3.Row, limit: int = 60, offset: int = 0) -> list[dict[str, Any]]:
    order = "i.position ASC" if playlist["kind"] == "user" else "i.added_at DESC, i.position DESC"
    rows = conn.execute(
        f"SELECT i.video_id AS id, COALESCE(v.title, '') AS title, v.channel_id, COALESCE(c.title, '') AS channel_title,"
        f" v.published_at, v.duration, v.view_count, p.position AS resume_position, p.completed AS completed,"
        f" {SEEN_SQL} AS seen, i.added_at"
        f" FROM playlist_items i LEFT JOIN videos v ON v.id = i.video_id LEFT JOIN channels c ON c.id = v.channel_id"
        f" LEFT JOIN video_progress p ON p.video_id = i.video_id"
        f" WHERE i.playlist_id = ? ORDER BY {order} LIMIT ? OFFSET ?", (playlist["id"], limit, offset)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["completed"], d["seen"] = bool(d["completed"]), bool(d["seen"])
        out.append(d)
    return out


def add(conn: sqlite3.Connection, playlist: sqlite3.Row, video_id: str, now: int | None = None) -> bool:
    """Put a video on a list. Returns False when it was already there (nothing changes, nothing is logged)."""
    now = now if now is not None else int(time.time())
    cur = conn.execute(
        "INSERT OR IGNORE INTO playlist_items(playlist_id, video_id, added_at, position)"
        " SELECT ?, ?, ?, COALESCE(MAX(position), 0) + 1 FROM playlist_items WHERE playlist_id = ?",
        (playlist["id"], video_id, now, playlist["id"]))
    if not cur.rowcount:
        return False
    conn.execute("UPDATE playlists SET updated_at=? WHERE id=?", (now, playlist["id"]))
    if playlist["kind"] in SYSTEM_EVENTS:
        log_event(conn, SYSTEM_EVENTS[playlist["kind"]][0], video_id=video_id, ts=now)
    else:
        log_event(conn, "playlist_add", video_id=video_id, payload={"playlist": playlist["name"]}, ts=now)
    return True


def remove(conn: sqlite3.Connection, playlist: sqlite3.Row, video_id: str, now: int | None = None) -> bool:
    now = now if now is not None else int(time.time())
    cur = conn.execute("DELETE FROM playlist_items WHERE playlist_id=? AND video_id=?", (playlist["id"], video_id))
    if not cur.rowcount:
        return False
    conn.execute("UPDATE playlists SET updated_at=? WHERE id=?", (now, playlist["id"]))
    if playlist["kind"] in SYSTEM_EVENTS:
        log_event(conn, SYSTEM_EVENTS[playlist["kind"]][1], video_id=video_id, ts=now)
    else:
        log_event(conn, "playlist_remove", video_id=video_id, payload={"playlist": playlist["name"]}, ts=now)
    return True


def membership(conn: sqlite3.Connection, video_id: str) -> dict[str, Any]:
    """Which of your lists a video is on: {'liked': bool, 'watch_later': bool, 'playlists': [ids of your own]}."""
    rows = conn.execute("SELECT p.id, p.kind FROM playlist_items i JOIN playlists p ON p.id = i.playlist_id"
                        " WHERE i.video_id = ?", (video_id,)).fetchall()
    return {"liked": any(r["kind"] == "liked" for r in rows), "watch_later": any(r["kind"] == "watch_later" for r in rows),
            "playlists": sorted(r["id"] for r in rows if r["kind"] == "user")}
