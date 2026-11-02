"""Search results, channel pages and playlists: cached extractor calls + local enrichment.

Everything returned is plain JSON-able dicts (the API sends them as-is). Lists are cached in
meta_cache; per-user state (resume position, known upload dates) is merged in AFTER the cache so
cached lists never go stale on that.
"""

from __future__ import annotations

import dataclasses
import hashlib
import sqlite3
from typing import Any

from ..db.queries import cache
from ..extract.base import (
    ChannelInfo, ChannelSummary, Extractor, PlaylistSummary, SearchItem, VideoSummary,
)

CACHE_VERSION = 1
TTL_SEARCH = 3600
TTL_CHANNEL = 900
TTL_PLAYLIST = 1800

_KIND = {VideoSummary: "video", ChannelSummary: "channel", PlaylistSummary: "playlist"}


def to_dict(item: SearchItem) -> dict[str, Any]:
    d = dataclasses.asdict(item)
    d["kind"] = _KIND[type(item)]
    return d


def enrich_videos(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fill gaps in video items from the local DB (the extractor's flat lists often lack the
    upload date, view count or channel name) and add resume/completed state."""
    ids = [i["id"] for i in items if i.get("kind") == "video"]
    if not ids:
        return items
    marks = ",".join("?" * len(ids))
    rows = {r["id"]: r for r in conn.execute(
        f"SELECT v.id, v.published_at, v.view_count, v.duration, c.title AS channel_title,"
        f" p.position AS resume_position, p.completed AS completed"
        f" FROM videos v LEFT JOIN channels c ON c.id = v.channel_id"
        f" LEFT JOIN video_progress p ON p.video_id = v.id WHERE v.id IN ({marks})", ids)}
    for i in items:
        if i.get("kind") != "video":
            continue
        r = rows.get(i["id"])
        for k in ("published_at", "view_count", "duration", "channel_title"):
            if i.get(k) is None and r is not None:
                i[k] = r[k]
        i["resume_position"] = r["resume_position"] if r else None
        i["completed"] = bool(r["completed"]) if r else False
    return items


class ListService:
    def __init__(self, conn: sqlite3.Connection, extractor: Extractor) -> None:
        self.conn, self.extractor = conn, extractor

    def _cached(self, key: str) -> Any | None:
        return cache.get(self.conn, f"list:v{CACHE_VERSION}:{key}")

    def _store(self, key: str, value: Any, ttl: int) -> None:
        cache.put(self.conn, f"list:v{CACHE_VERSION}:{key}", value, ttl)

    async def search(self, query: str, kind: str, limit: int = 20) -> list[dict[str, Any]]:
        key = f"search:{kind}:{limit}:{hashlib.sha1(query.strip().lower().encode()).hexdigest()}"
        items = self._cached(key)
        if items is None:
            items = [to_dict(i) for i in await self.extractor.search(query, kind, limit)]
            self._store(key, items, TTL_SEARCH)
        return enrich_videos(self.conn, items)

    async def channel(self, channel_id: str, limit: int = 30, sort: str = "newest") -> dict[str, Any]:
        key = f"channel:{channel_id}:{sort}:{limit}"
        page = self._cached(key)
        if page is None:
            info, videos = await self.extractor.channel_page(channel_id, limit, sort=sort)
            page = {"channel": dataclasses.asdict(info), "videos": [to_dict(v) for v in videos]}
            self._store(key, page, TTL_CHANNEL)
        page["videos"] = enrich_videos(self.conn, page["videos"])
        page["sort"] = sort
        page["subscribed"] = self.conn.execute(
            "SELECT 1 FROM subscriptions WHERE channel_id=?", (channel_id,)).fetchone() is not None
        return page

    async def playlist(self, playlist_id: str, limit: int = 100) -> dict[str, Any]:
        key = f"playlist:{playlist_id}:{limit}"
        page = self._cached(key)
        if page is None:
            head, videos = await self.extractor.playlist_page(playlist_id, limit)
            page = {"playlist": dataclasses.asdict(head), "videos": [to_dict(v) for v in videos]}
            self._store(key, page, TTL_PLAYLIST)
        page["videos"] = enrich_videos(self.conn, page["videos"])
        return page
