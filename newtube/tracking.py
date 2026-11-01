"""Watch tracking: played intervals, completion, impressions.

The player reports *intervals* of video time actually played, e.g. [[0, 120], [300, 340]].
From them (stored per watch session) the recommender can later derive watched fraction,
rewatches (overlap across sessions) and skipped sections.
"""

from __future__ import annotations

import json
import sqlite3
import time

Interval = tuple[float, float]
MAX_INTERVALS = 2000
COMPLETE_FRACTION = 0.9


def merge_intervals(intervals: list[Interval]) -> list[Interval]:
    """Union of intervals, sorted and non-overlapping. Intervals touching within 0.5 s are joined."""
    out: list[Interval] = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1] + 0.5:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def total_length(intervals: list[Interval]) -> float:
    return sum(b - a for a, b in intervals)


def sanitize(raw: list[list[float]] | list[Interval], duration: float | None) -> list[Interval]:
    """Drop malformed intervals and clamp to [0, duration]. Never trust the browser."""
    out: list[Interval] = []
    for item in raw[:MAX_INTERVALS]:
        try:
            a, b = float(item[0]), float(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        a = max(0.0, a)
        if duration:
            a, b = min(a, duration), min(b, duration)
        if b > a:
            out.append((a, b))
    return out


def record_watch(conn: sqlite3.Connection, *, session_id: str, video_id: str, source: str | None,
                 position: float, duration: float | None, intervals: list[list[float]],
                 ended: bool, now: int | None = None) -> None:
    """Upsert a watch session and the per-video resume point."""
    now = now if now is not None else int(time.time())
    new = sanitize(intervals, duration)
    row = conn.execute("SELECT intervals, max_position FROM watch_sessions WHERE id=?",
                       (session_id,)).fetchone()
    old = [tuple(i) for i in json.loads(row["intervals"])] if row else []
    merged = merge_intervals(old + new)
    max_pos = max(position, row["max_position"] if row else 0.0)
    if row:
        conn.execute("UPDATE watch_sessions SET intervals=?, max_position=?, duration=COALESCE(?,duration),"
                     " ended_at=? WHERE id=?",
                     (json.dumps(merged), max_pos, duration, now if ended else None, session_id))
    else:
        conn.execute("INSERT INTO watch_sessions(id,video_id,started_at,ended_at,source,intervals,"
                     " max_position,duration) VALUES (?,?,?,?,?,?,?,?)",
                     (session_id, video_id, now, now if ended else None, source,
                      json.dumps(merged), max_pos, duration))
    # Resume point is based on *all* of this video's sessions, not just this one.
    completed = 0
    if duration:
        all_iv: list[Interval] = []
        for r in conn.execute("SELECT intervals FROM watch_sessions WHERE video_id=?", (video_id,)):
            all_iv += [tuple(i) for i in json.loads(r["intervals"])]
        fraction = total_length(merge_intervals(all_iv)) / duration
        completed = int(fraction >= COMPLETE_FRACTION or position >= duration - 10)
    conn.execute(
        "INSERT INTO video_progress(video_id,position,duration,completed,updated_at) VALUES (?,?,?,?,?)"
        " ON CONFLICT(video_id) DO UPDATE SET position=excluded.position,"
        "  duration=COALESCE(excluded.duration,duration), completed=excluded.completed,"
        "  updated_at=excluded.updated_at",
        (video_id, position, duration, completed, now))


def watched_fraction(conn: sqlite3.Connection, video_id: str) -> float:
    """Fraction of the video ever played, across all sessions (0..1)."""
    d = conn.execute("SELECT duration FROM video_progress WHERE video_id=?", (video_id,)).fetchone()
    if not d or not d["duration"]:
        return 0.0
    iv: list[Interval] = []
    for r in conn.execute("SELECT intervals FROM watch_sessions WHERE video_id=?", (video_id,)):
        iv += [tuple(i) for i in json.loads(r["intervals"])]
    return min(1.0, total_length(merge_intervals(iv)) / d["duration"])


def record_impression(conn: sqlite3.Connection, *, video_id: str, surface: str,
                      position: int | None, request_id: str | None, visible_ms: int | None,
                      now: int | None = None) -> None:
    """One row per (request_id, video): a repeat report only extends visible_ms."""
    now = now if now is not None else int(time.time())
    if request_id:
        row = conn.execute("SELECT id, visible_ms FROM impressions WHERE request_id=? AND video_id=?",
                           (request_id, video_id)).fetchone()
        if row:
            conn.execute("UPDATE impressions SET visible_ms=? WHERE id=?",
                         (max(row["visible_ms"] or 0, visible_ms or 0), row["id"]))
            return
    conn.execute("INSERT INTO impressions(ts,video_id,surface,position,request_id,visible_ms)"
                 " VALUES (?,?,?,?,?,?)", (now, video_id, surface, position, request_id, visible_ms))
