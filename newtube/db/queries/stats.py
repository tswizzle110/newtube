"""How much you have watched: the time actually spent playing videos in newtube, in total and day by day.

Only real playback counts. Each watch session records which stretches of the video were played (merged, so seeking
back and replaying the same minute inside one session is not counted twice, and never more than the video's length).
Imported Takeout history says a video was opened but not for how long, so it cannot count. Watches you removed from
History do not count either.

The time is VIDEO time: at 2x speed an hour of video takes half an hour of your day, and counts as an hour. Days are
your own calendar days (``tz``), and a session counts on the day it started."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import time
from collections import defaultdict
from typing import Any
from zoneinfo import ZoneInfo

from ...tracking import merge_intervals, total_length
from . import settings
from .seen import not_hidden

MAX_DAYS = 730           # the per-day list never grows past two years (the total always covers everything)
TOP = 5


def parse_day(text: str) -> dt.date:
    """'2026-11-02' -> date. Raises ValueError for anything else."""
    return dt.date.fromisoformat(text)


def saved_since(conn: sqlite3.Connection) -> dt.date | None:
    value = settings.get(conn, "stats_since")
    try:
        return parse_day(value) if value else None
    except (TypeError, ValueError):
        return None


def save_since(conn: sqlite3.Connection, day: dt.date | None) -> None:
    settings.put(conn, "stats_since", day.isoformat() if day else None)


def _session_seconds(raw_intervals: str, duration: float | None) -> float:
    try:
        spans = [tuple(i) for i in json.loads(raw_intervals)]
    except (TypeError, ValueError):
        return 0.0
    seconds = total_length(merge_intervals(spans))
    return min(seconds, duration) if duration else seconds


MAX_SKIP = 3600          # one skip never counts for more than an hour (guards a bad segment)
NOT_SKIPS = {"poi_highlight"}   # "jump to the highlight" moves you forward but is not a sponsor/intro/etc. being skipped


def _skipped(conn: sqlite3.Connection, tz: ZoneInfo, start_ts: int) -> dict[str, Any]:
    """Video time SponsorBlock saved you: every automatic or button skip, minus the ones you undid. Read from the
    events the player already logs (the segment's start and end), so skips made before this was added count too.
    Each skip counts, including skipping the same sponsor again after rewinding; an undo cancels the latest skip of
    that same segment. Attributed to the day of the skip, like watch time."""
    pending: dict[tuple[str | None, str], list[tuple[dt.date, str, float]]] = defaultdict(list)
    per_day: dict[dt.date, float] = defaultdict(float)
    per_category: dict[str, float] = defaultdict(float)
    count = 0
    for r in conn.execute(
            "SELECT ts, type, video_id, payload FROM events WHERE type IN ('sb_skip','sb_manual_skip','sb_undo')"
            " AND ts >= ? ORDER BY ts, id", (start_ts,)):
        try:
            p = json.loads(r["payload"] or "{}")
            seg_start, seg_end = float(p["start"]), float(p["end"])
        except (TypeError, ValueError, KeyError):
            continue
        category, uuid = str(p.get("category") or "other"), str(p.get("uuid") or "")
        if category in NOT_SKIPS:
            continue
        key = (r["video_id"], uuid)
        day = dt.datetime.fromtimestamp(r["ts"], tz).date()
        if r["type"] == "sb_undo":
            if pending[key]:
                d, c, s = pending[key].pop()
                per_day[d] -= s
                per_category[c] -= s
                count -= 1
            continue
        seconds = min(max(seg_end - seg_start, 0.0), MAX_SKIP)
        if seconds <= 0:
            continue
        pending[key].append((day, category, seconds))
        per_day[day] += seconds
        per_category[category] += seconds
        count += 1
    return {"seconds": round(max(0.0, sum(per_day.values()))), "count": max(0, count),
            "days": {d: max(0.0, s) for d, s in per_day.items()},
            "categories": sorted(({"category": c, "seconds": round(s)} for c, s in per_category.items() if s > 0.5),
                                 key=lambda x: -x["seconds"])}


def watch_stats(conn: sqlite3.Connection, tz: ZoneInfo, now: int | None = None) -> dict[str, Any]:
    now = now if now is not None else int(time.time())
    today = dt.datetime.fromtimestamp(now, tz).date()
    visible = not_hidden("w.video_id", "w.started_at")

    first = conn.execute(f"SELECT min(w.started_at) FROM watch_sessions w WHERE {visible}").fetchone()[0]
    chosen = saved_since(conn)
    since = chosen or (dt.datetime.fromtimestamp(first, tz).date() if first else today)
    start_ts = int(dt.datetime.combine(since, dt.time.min, tz).timestamp())

    per_day: dict[dt.date, float] = defaultdict(float)
    per_video: dict[str, float] = defaultdict(float)
    per_channel: dict[str | None, float] = defaultdict(float)
    titles: dict[str, str] = {}
    channel_titles: dict[str, str] = {}
    sessions = 0
    for r in conn.execute(
            f"SELECT w.video_id, w.started_at, w.intervals, w.duration, v.title AS vtitle, v.channel_id, c.title AS ctitle"
            f" FROM watch_sessions w LEFT JOIN videos v ON v.id = w.video_id LEFT JOIN channels c ON c.id = v.channel_id"
            f" WHERE w.started_at >= ? AND {visible}", (start_ts,)):
        seconds = _session_seconds(r["intervals"], r["duration"])
        if seconds <= 0:
            continue
        sessions += 1
        per_day[dt.datetime.fromtimestamp(r["started_at"], tz).date()] += seconds
        per_video[r["video_id"]] += seconds
        titles.setdefault(r["video_id"], r["vtitle"] or r["video_id"])
        per_channel[r["channel_id"]] += seconds
        if r["channel_id"] and r["ctitle"]:
            channel_titles[r["channel_id"]] = r["ctitle"]

    skipped = _skipped(conn, tz, start_ts)
    total = sum(per_day.values())
    span = max(1, (today - since).days + 1)
    first_listed = max(since, today - dt.timedelta(days=MAX_DAYS - 1))
    days = [{"date": d.isoformat(), "seconds": round(per_day.get(d, 0.0)),
             "skipped": round(skipped["days"].get(d, 0.0))}
            for d in (first_listed + dt.timedelta(days=i) for i in range((today - first_listed).days + 1))]
    best = max(per_day.items(), key=lambda kv: kv[1], default=None)
    return {
        "since": since.isoformat(), "since_is_automatic": chosen is None, "today": today.isoformat(), "tz": str(tz),
        "total_seconds": round(total), "sessions": sessions, "videos": len(per_video), "days_counted": span,
        "active_days": sum(1 for s in per_day.values() if s > 0),
        "average_per_day": round(total / span),
        "longest_day": {"date": best[0].isoformat(), "seconds": round(best[1])} if best else None,
        "days": days,
        "skipped": {"seconds": skipped["seconds"], "count": skipped["count"], "categories": skipped["categories"]},
        "top_videos": [{"id": v, "title": titles[v], "seconds": round(s)}
                       for v, s in sorted(per_video.items(), key=lambda kv: -kv[1])[:TOP]],
        "top_channels": [{"id": c, "title": channel_titles.get(c, ""), "seconds": round(s)}
                         for c, s in sorted(((c, s) for c, s in per_channel.items() if c), key=lambda kv: -kv[1])[:TOP]],
    }
