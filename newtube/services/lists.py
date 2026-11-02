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
SCAN_LIMIT = 500      # uploads read to sort a channel; larger channels get a partial Popular and no Oldest

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

    async def _header(self, channel_id: str) -> dict[str, Any]:
        """The channel's name/avatar/description: stored by the newest-first page if that loaded first."""
        key = f"channel_header:{channel_id}"
        header = self._cached(key)
        if header is None:
            info, _ = await self.extractor.channel_page(channel_id, 1)    # 1 request, just for the header
            header = dataclasses.asdict(info)
            self._store(key, header, TTL_CHANNEL * 4)
        return header

    async def _uploads(self, channel_id: str) -> dict[str, Any]:
        key = f"uploads:{channel_id}"
        up = self._cached(key)
        if up is None:
            videos, total = await self.extractor.channel_uploads(channel_id, SCAN_LIMIT)
            up = {"videos": [to_dict(v) for v in videos], "total": total}
            self._store(key, up, TTL_SEARCH)
        return up

    async def channel(self, channel_id: str, limit: int = 30, sort: str = "newest") -> dict[str, Any]:
        """``newest`` comes straight from the Videos tab. ``popular`` and ``oldest`` are made from the
        channel's uploads list (read once, cached) because YouTube ignores sort options on the tab and
        its entries have no view counts; that list has them. Sorting happens here, so switching sort or
        loading more costs no further requests."""
        extra: dict[str, Any] = {}
        if sort == "newest":
            key = f"channel:{channel_id}:{limit}"
            page = self._cached(key)
            if page is None:
                info, videos = await self.extractor.channel_page(channel_id, limit)
                page = {"channel": dataclasses.asdict(info), "videos": [to_dict(v) for v in videos]}
                self._store(key, page, TTL_CHANNEL)
                self._store(f"channel_header:{channel_id}", page["channel"], TTL_CHANNEL * 4)
            header, videos_out = page["channel"], page["videos"]
            extra["has_more"] = len(videos_out) >= limit
        else:
            up = await self._uploads(channel_id)
            header = await self._header(channel_id)
            videos_out, total, scanned = list(up["videos"]), up["total"], len(up["videos"])
            partial = (total is not None and total > scanned) or (total is None and scanned >= SCAN_LIMIT)
            if sort == "popular":
                videos_out.sort(key=lambda v: v.get("view_count") or -1, reverse=True)   # stable: ties stay newest-first
                if partial:
                    extra["notice"] = f"Most viewed among the newest {scanned} of {total or 'many'} videos."
            elif partial:
                videos_out = []
                extra["unavailable"] = (f"Oldest needs the channel's whole upload list, and this channel has "
                                        f"{total or 'more than ' + str(SCAN_LIMIT)} videos (the limit is {SCAN_LIMIT}).")
            else:
                videos_out.reverse()
            extra["has_more"] = len(videos_out) > limit
            videos_out = videos_out[:limit]
        videos_out = enrich_videos(self.conn, [dict(v) for v in videos_out])
        return {"channel": header, "videos": videos_out, "sort": sort, **extra,
                "subscribed": self.conn.execute("SELECT 1 FROM subscriptions WHERE channel_id=?",
                                                (channel_id,)).fetchone() is not None}

    async def playlist(self, playlist_id: str, limit: int = 100) -> dict[str, Any]:
        key = f"playlist:{playlist_id}:{limit}"
        page = self._cached(key)
        if page is None:
            head, videos = await self.extractor.playlist_page(playlist_id, limit)
            page = {"playlist": dataclasses.asdict(head), "videos": [to_dict(v) for v in videos]}
            self._store(key, page, TTL_PLAYLIST)
        page["videos"] = enrich_videos(self.conn, page["videos"])
        return page
