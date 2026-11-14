"""Video metadata service: TTL cache + persistence + de-duplicated fetches.

The cached blob includes signed googlevideo URLs, which expire after a few
hours, so one TTL covers everything for now. (Later we can cache the slow-changing
metadata longer than the stream URLs.)
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sqlite3
import re
import time
from typing import Any

from ..db.queries import cache
from ..extract.base import Caption, Chapter, ChannelInfo, Extractor, Format, Storyboard, VideoInfo

INFO_TTL = 4 * 3600          # used when the stream links carry no expiry time
MAX_TTL = 5 * 3600           # even when they are good for longer
EXPIRY_MARGIN = 30 * 60      # stop using the links this long before they die, so a video never starts on a dying link
MIN_TTL = 5 * 60
# Bump when the fields we cache change (e.g. a new Format attribute), so entries cached by older
# code are ignored instead of being served with missing data for up to INFO_TTL.
CACHE_VERSION = 3


def cache_seconds(info: VideoInfo, now: int | None = None) -> int:
    """How long the cached copy (which includes signed stream links) stays usable: until shortly before the EARLIEST
    link expires (googlevideo URLs say when, in ``expire=``). A fixed time was wrong both ways: coming back to a video
    at hour 5 needed a lookup the link's real lifetime made unnecessary, and a link that died sooner than that failed
    the player, which then had to look the video up again before playing. Without an expiry, the old fixed time."""
    now = now if now is not None else int(time.time())
    expiries = [int(m.group(1)) for f in info.formats if (m := re.search(r"[?&/]expire[=/](\d{9,11})", f.url or ""))]
    if not expiries:
        return INFO_TTL
    return max(MIN_TTL, min(MAX_TTL, min(expiries) - now - EXPIRY_MARGIN))


def info_from_dict(d: dict[str, Any]) -> VideoInfo:
    d = dict(d)
    d["chapters"] = [Chapter(**c) for c in d.get("chapters", [])]
    d["formats"] = [Format(**f) for f in d.get("formats", [])]
    d["captions"] = [Caption(**c) for c in d.get("captions", [])]
    d["storyboard"] = Storyboard(**d["storyboard"]) if d.get("storyboard") else None
    return VideoInfo(**d)


class VideoService:
    def __init__(self, conn: sqlite3.Connection, extractor: Extractor) -> None:
        self.conn, self.extractor = conn, extractor
        self._inflight: dict[str, asyncio.Task[VideoInfo]] = {}

    async def get(self, video_id: str, *, refresh: bool = False,
                  background: bool = False) -> VideoInfo:
        key = f"video:v{CACHE_VERSION}:{video_id}"
        if not refresh:
            hit = cache.get(self.conn, key)
            if hit:
                return info_from_dict(hit)
        # Several simultaneous requests for one video (e.g. a player firing parallel
        # range requests after a 403) share a single extractor call.
        task = self._inflight.get(video_id)
        if task is None:
            task = asyncio.create_task(self._fetch(video_id, background))
            self._inflight[video_id] = task
            task.add_done_callback(lambda _t: self._inflight.pop(video_id, None))
        return await task

    async def _fetch(self, video_id: str, background: bool) -> VideoInfo:
        info = await self.extractor.video_info(video_id, background=background)
        self.persist(info)
        cache.put(self.conn, f"video:v{CACHE_VERSION}:{video_id}", dataclasses.asdict(info), cache_seconds(info))
        return info

    def persist(self, v: VideoInfo) -> None:
        c = self.conn
        c.execute("BEGIN")
        try:
            if v.channel_id and v.channel_title:
                c.execute("INSERT INTO channels(id,title,created_at) VALUES (?,?,?)"
                          " ON CONFLICT(id) DO UPDATE SET title=excluded.title",
                          (v.channel_id, v.channel_title, int(time.time())))
            c.execute(
                "INSERT INTO videos(id,channel_id,title,description,published_at,duration,"
                " view_count,category,tags,is_live,thumbnail_url,first_seen_at,detail_fetched_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET channel_id=excluded.channel_id,"
                "  title=excluded.title, description=excluded.description,"
                "  published_at=COALESCE(excluded.published_at, published_at),"
                "  duration=excluded.duration, view_count=excluded.view_count,"
                "  category=excluded.category, tags=excluded.tags, is_live=excluded.is_live,"
                "  thumbnail_url=COALESCE(excluded.thumbnail_url, thumbnail_url),"
                "  detail_fetched_at=excluded.detail_fetched_at",
                (v.id, v.channel_id, v.title, v.description, v.published_at, v.duration,
                 v.view_count, v.category, json.dumps(v.tags), int(v.is_live),
                 v.thumbnail_url, v.fetched_at, v.fetched_at),
            )
            c.execute("DELETE FROM chapters WHERE video_id=?", (v.id,))
            c.executemany("INSERT INTO chapters(video_id,idx,start,title) VALUES (?,?,?,?)",
                          [(v.id, i, ch.start, ch.title) for i, ch in enumerate(v.chapters)])
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
