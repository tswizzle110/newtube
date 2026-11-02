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

import logging

from ..db.queries import cache, uploads as stored
from ..extract.base import (
    ChannelInfo, ChannelSummary, ExtractionError, Extractor, PlaylistSummary, SearchItem, VideoSummary,
)

CACHE_VERSION = 1
TTL_SEARCH = 3600
TTL_CHANNEL = 900
TTL_PLAYLIST = 1800
SCAN_LIMIT = 10_000   # uploads read to sort a channel (about 100 requests); beyond that Popular is partial and Oldest unavailable
FULL_RESCAN_AFTER = 30 * 24 * 3600   # a stored upload list is re-read in full this often (refreshes view counts)
TOPUP_AFTER = 6 * 3600               # ...and checked for NEW uploads (one request) when opened after this long
TOPUP_TIMEOUT = 15                   # seconds; a slow/failed top-up just serves the stored list
TOPUP_PAGE = 100
log = logging.getLogger("newtube.lists")

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
    explicit: bool = False        # you asked for it (Rescan / first visit): show progress rather than the old list
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

    def _start_scan(self, channel_id: str, explicit: bool) -> _Scan:
        scan = _Scan(explicit=explicit)

        def progress(done: int, total: int | None = None) -> None:     # called from yt-dlp's worker thread
            scan.done = done
            scan.total = total or scan.total

        async def run() -> None:
            try:
                async with self._scan_gate:
                    videos, total = await self.extractor.channel_uploads(channel_id, SCAN_LIMIT, progress)
                truncated = len(videos) >= SCAN_LIMIT and (total is None or total > len(videos))
                stored.store_scan(self.conn, channel_id, videos, total, truncated)
                self._scans.pop(channel_id, None)
            except Exception as e:  # noqa: BLE001 - reported to the next request, then forgotten
                scan.error = str(e) or type(e).__name__
                log.warning("uploads scan of %s failed: %s", channel_id, scan.error)
            finally:
                scan.finished = True

        scan.task = asyncio.create_task(run())
        self._scans[channel_id] = scan
        return scan

    async def _top_up(self, channel_id: str) -> bool:
        """Look for new uploads (one request). Returns False if a full rescan is needed instead."""
        try:
            newest = await asyncio.wait_for(self.extractor.recent_uploads(channel_id, TOPUP_PAGE), TOPUP_TIMEOUT)
        except Exception as e:  # noqa: BLE001 - serve what we have; try again next time
            log.info("top-up of %s skipped: %s", channel_id, e)
            return True
        return stored.top_up(self.conn, channel_id, newest) == "ok"

    async def channel(self, channel_id: str, limit: int = 30, sort: str = "newest", offset: int = 0,
                      refresh: bool = False) -> dict[str, Any]:
        """``newest`` comes from the Videos tab (at most 150 deep, no view counts).

        ``popular`` and ``oldest`` need the channel's WHOLE upload list, because YouTube only pages
        forwards (the oldest videos are at the very end) and ignores sort options on the Videos tab.
        That list (the "uploads" playlist: it has view counts) is read ONCE in the background, with
        progress, and then KEPT (about 115 bytes per video). Afterwards:
        * opening the channel after TOPUP_AFTER reads only the newest page (1 request) and adds
          whatever is new; if a whole page is unseen, something is missing and it rescans in full;
        * after FULL_RESCAN_AFTER the whole list is re-read in the background (view counts drift),
          while you keep using the old one;
        * "Rescan" re-reads it on request, showing progress.
        Sorting and paging come from the stored list and cost no requests. While a scan you are
        waiting for runs, the reply has ``scanning`` instead of videos."""
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

        info = stored.scan_info(self.conn, channel_id)
        scan = self._scans.get(channel_id)
        refreshing = False
        if scan is not None and scan.finished and scan.error:
            del self._scans[channel_id]                  # a failure is reported once; the next try starts afresh
            if info is None or scan.explicit:
                raise ExtractionError(scan.error)
            scan = None                                  # a background refresh failed: keep serving the stored list
        if refresh and scan is None:
            scan = self._start_scan(channel_id, explicit=True)
        elif info is None and scan is None:
            scan = self._start_scan(channel_id, explicit=True)
        elif info is not None and scan is None:
            now = time.time()
            if now - info["scanned_at"] > FULL_RESCAN_AFTER:
                scan = self._start_scan(channel_id, explicit=False)      # monthly refresh, behind the scenes
            elif now - info["topped_up_at"] > TOPUP_AFTER and not await self._top_up(channel_id):
                scan = self._start_scan(channel_id, explicit=False)      # a gap: re-read everything
                log.info("%s: a whole page of unseen uploads, rescanning", channel_id)
                info = stored.scan_info(self.conn, channel_id)
        if scan is not None and (scan.explicit or info is None):
            return {"channel": await self._header(channel_id), "videos": [], "sort": sort, "subscribed": subscribed,
                    "scanning": {"done": scan.done, "total": scan.total}, "has_more": False}
        refreshing = scan is not None

        info = stored.scan_info(self.conn, channel_id)
        n = stored.count(self.conn, channel_id)
        header = await self._header(channel_id)
        extra: dict[str, Any] = {}
        truncated = bool(info["truncated"])
        if sort == "oldest" and truncated:
            videos_out: list[dict[str, Any]] = []
            extra["unavailable"] = (f"Oldest needs this channel's whole upload list, and it has "
                                    f"{info['total'] or 'more than ' + str(SCAN_LIMIT)} videos (the limit is {SCAN_LIMIT}).")
        else:
            videos_out = stored.page(self.conn, channel_id, sort, offset, limit)
            if sort == "popular" and truncated:
                extra["notice"] = f"Most viewed among the newest {n} of {info['total'] or 'many'} videos."
        for v in videos_out:
            v["channel_title"] = header["title"]
        videos_out = enrich_videos(self.conn, videos_out)
        return {"channel": header, "videos": videos_out, "sort": sort, "subscribed": subscribed,
                "has_more": offset + limit < n and "unavailable" not in extra, "total_videos": n,
                "fetched_at": info["scanned_at"], "checked_at": info["topped_up_at"],
                "refreshing": refreshing, **extra}

    async def playlist(self, playlist_id: str, limit: int = 100) -> dict[str, Any]:
        key = f"playlist:{playlist_id}:{limit}"
        page = self._cached(key)
        if page is None:
            head, videos = await self.extractor.playlist_page(playlist_id, limit)
            page = {"playlist": dataclasses.asdict(head), "videos": [to_dict(v) for v in videos]}
            self._store(key, page, TTL_PLAYLIST)
        page["videos"] = enrich_videos(self.conn, page["videos"])
        return page
