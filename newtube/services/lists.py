"""Search results, channel pages and playlists: cached extractor calls + local enrichment.

Everything returned is plain JSON-able dicts (the API sends them as-is). Lists are cached in
meta_cache; per-user state (resume position, known upload dates) is merged in AFTER the cache so
cached lists never go stale on that.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from ..db.queries import cache
from ..extract.base import (
    ChannelInfo, ChannelSummary, ExtractionError, Extractor, PlaylistSummary, SearchItem, VideoSummary,
)

CACHE_VERSION = 1
TTL_SEARCH = 3600
TTL_CHANNEL = 900
TTL_PLAYLIST = 1800
SCAN_LIMIT = 10_000   # uploads read to sort a channel (about 100 requests); beyond that Popular is partial and Oldest unavailable
UPLOADS_TTL = 24 * 3600   # a scanned list is reused for a day (the oldest videos never change)

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


@dataclass
class _Scan:
    """One channel's uploads being read in the background."""
    done: int = 0
    total: int | None = None
    error: str | None = None
    finished: bool = False
    task: asyncio.Task[None] | None = None


class ListService:
    def __init__(self, conn: sqlite3.Connection, extractor: Extractor) -> None:
        self.conn, self.extractor = conn, extractor
        self._scans: dict[str, _Scan] = {}
        self._scan_gate = asyncio.Semaphore(1)       # one long scan at a time: never a pile of them

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

    def _uploads_cached(self, channel_id: str) -> dict[str, Any] | None:
        return self._cached(f"uploads2:{channel_id}")

    def _start_scan(self, channel_id: str) -> _Scan:
        scan = _Scan()

        def progress(done: int, total: int | None = None) -> None:     # called from yt-dlp's worker thread
            scan.done = done
            scan.total = total or scan.total

        async def run() -> None:
            try:
                async with self._scan_gate:
                    videos, total = await self.extractor.channel_uploads(channel_id, SCAN_LIMIT, progress)
                truncated = len(videos) >= SCAN_LIMIT and (total is None or total > len(videos))
                self._store(f"uploads2:{channel_id}", {
                    "videos": [to_dict(v) for v in videos], "total": total, "truncated": truncated,
                    "fetched_at": int(time.time())}, UPLOADS_TTL)
                self._scans.pop(channel_id, None)
            except Exception as e:  # noqa: BLE001 - reported to the next request, then forgotten
                scan.error = str(e) or type(e).__name__
            finally:
                scan.finished = True

        scan.task = asyncio.create_task(run())
        self._scans[channel_id] = scan
        return scan

    async def channel(self, channel_id: str, limit: int = 30, sort: str = "newest", offset: int = 0,
                      refresh: bool = False) -> dict[str, Any]:
        """``newest`` comes from the Videos tab (at most 150 deep, no view counts).

        ``popular`` and ``oldest`` need the channel's WHOLE upload list, because YouTube only pages
        forwards (the oldest videos are at the very end) and ignores sort options on the Videos tab.
        That list (the "uploads" playlist: it has view counts) is read ONCE in the background, with
        progress, and cached for a day; after that sorting and paging are served from the cache and
        cost no requests. While a scan runs the reply has ``scanning`` instead of videos."""
        subscribed = self.conn.execute("SELECT 1 FROM subscriptions WHERE channel_id=?",
                                       (channel_id,)).fetchone() is not None
        if sort == "newest":
            want = min(offset + limit, 150)
            key = f"channel:{channel_id}:{want}"
            page = self._cached(key)
            if page is None:
                info, videos = await self.extractor.channel_page(channel_id, want)
                page = {"channel": dataclasses.asdict(info), "videos": [to_dict(v) for v in videos]}
                self._store(key, page, TTL_CHANNEL)
                self._store(f"channel_header:{channel_id}", page["channel"], TTL_CHANNEL * 4)
            out = enrich_videos(self.conn, [dict(v) for v in page["videos"][offset:offset + limit]])
            return {"channel": page["channel"], "videos": out, "sort": sort, "subscribed": subscribed,
                    "has_more": len(page["videos"]) >= want and want < 150}

        up = self._uploads_cached(channel_id)
        scan = self._scans.get(channel_id)
        if refresh and scan is None:
            up = None                                    # "Rescan": ignore the cached list
        if up is None:
            if scan is not None and scan.finished and scan.error:
                del self._scans[channel_id]              # report the failure once; the next try starts afresh
                raise ExtractionError(scan.error)
            if scan is None:
                scan = self._start_scan(channel_id)
            return {"channel": await self._header(channel_id), "videos": [], "sort": sort, "subscribed": subscribed,
                    "scanning": {"done": scan.done, "total": scan.total}, "has_more": False}

        videos_all, total, truncated = list(up["videos"]), up["total"], up["truncated"]
        extra: dict[str, Any] = {}
        if sort == "popular":
            videos_all.sort(key=lambda v: v.get("view_count") or -1, reverse=True)   # stable: ties stay newest-first
            if truncated:
                extra["notice"] = f"Most viewed among the newest {len(videos_all)} of {total or 'many'} videos."
        elif truncated:
            videos_all = []
            extra["unavailable"] = (f"Oldest needs this channel's whole upload list, and it has "
                                    f"{total or 'more than ' + str(SCAN_LIMIT)} videos (the limit is {SCAN_LIMIT}).")
        else:
            videos_all.reverse()
        out = enrich_videos(self.conn, [dict(v) for v in videos_all[offset:offset + limit]])
        return {"channel": await self._header(channel_id), "videos": out, "sort": sort, "subscribed": subscribed,
                "has_more": offset + limit < len(videos_all), "total_videos": len(up["videos"]),
                "fetched_at": up["fetched_at"], **extra}

    async def playlist(self, playlist_id: str, limit: int = 100) -> dict[str, Any]:
        key = f"playlist:{playlist_id}:{limit}"
        page = self._cached(key)
        if page is None:
            head, videos = await self.extractor.playlist_page(playlist_id, limit)
            page = {"playlist": dataclasses.asdict(head), "videos": [to_dict(v) for v in videos]}
            self._store(key, page, TTL_PLAYLIST)
        page["videos"] = enrich_videos(self.conn, page["videos"])
        return page
