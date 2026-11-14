"""Search that never leaves this server: suggestions from what you searched before, and a search inside one channel's
videos that newtube already has saved."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

MAX_QUERY = 200


def suggestions(conn: sqlite3.Connection, prefix: str = "", limit: int = 8) -> list[str]:
    """Your own past searches that start with (or, failing that, contain) what you have typed, most recent first.
    With nothing typed: your latest searches."""
    needle = prefix.strip().lower()
    seen: dict[str, str] = {}
    for r in conn.execute("SELECT payload FROM events WHERE type='search' ORDER BY id DESC LIMIT 3000"):
        try:
            q = str(json.loads(r["payload"] or "{}").get("q") or "").strip()
        except (TypeError, ValueError):
            continue
        if q and q.lower() not in seen and len(q) <= MAX_QUERY:
            seen[q.lower()] = q
    queries = list(seen.values())                                   # newest first
    if not needle:
        return queries[:limit]
    starts = [q for q in queries if q.lower().startswith(needle) and q.lower() != needle]
    inside = [q for q in queries if needle in q.lower() and not q.lower().startswith(needle)]
    return (starts + inside)[:limit]


def channel_videos(conn: sqlite3.Connection, channel_id: str, query: str, limit: int = 40) -> tuple[list[dict[str, Any]], int]:
    """Videos of this channel whose title contains the query: from its saved upload list (every upload, once you have sorted the
    channel by Popular or Oldest) and from any video of it that was ever stored. Returns (videos, how many were searched)."""
    needle = query.strip().lower()
    searched = conn.execute(
        "SELECT count(*) FROM (SELECT video_id FROM channel_uploads WHERE channel_id=? UNION SELECT id FROM videos"
        " WHERE channel_id=? AND title <> '')", (channel_id, channel_id)).fetchone()[0]
    if not needle:
        return [], searched
    rows = conn.execute(
        "SELECT x.id, x.title, x.duration, x.view_count FROM ("
        "  SELECT u.video_id AS id, u.title AS title, u.duration AS duration, u.view_count AS view_count, u.pos AS rank"
        "    FROM channel_uploads u WHERE u.channel_id = ?"
        "  UNION ALL"
        "  SELECT v.id, v.title, v.duration, v.view_count, 100000 - COALESCE(v.published_at, 0) / 100000 FROM videos v"
        "    WHERE v.channel_id = ? AND v.title <> '' AND v.id NOT IN (SELECT video_id FROM channel_uploads WHERE channel_id = ?)"
        ") x WHERE instr(lower(x.title), ?) > 0 ORDER BY x.rank LIMIT ?", (channel_id, channel_id, channel_id, needle, limit)).fetchall()
    title = conn.execute("SELECT title FROM channels WHERE id=?", (channel_id,)).fetchone()
    return ([{"id": r["id"], "title": r["title"], "channel_id": channel_id, "channel_title": title[0] if title else "",
              "duration": r["duration"], "view_count": r["view_count"], "kind": "video"} for r in rows], searched)
