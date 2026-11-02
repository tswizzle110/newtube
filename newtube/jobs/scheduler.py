"""Background work, always through the BACKGROUND lane of the rate limiter.

Phase 1: poll subscribed channels' RSS feeds. Each tick handles at most one
channel, then sleeps a jittered interval, so polling is spread out instead of
bursty. (Phase 4 adds metadata backfill and related-video lookups here.)
"""

from __future__ import annotations

import asyncio
import logging
import random
import sqlite3
import time
from collections.abc import Awaitable, Callable

from ..config import Settings
from ..db.queries import subscriptions as subs
from ..extract.base import ExtractionError, Extractor
from ..extract.rss import feed_url, parse_feed
from ..net.http import OutboundClient
from ..net.ratelimit import DailyCapReached, Lane

log = logging.getLogger("newtube.jobs")
RETRY_AFTER = 600   # seconds before retrying a channel whose poll failed completely
RSS_FAILURES_BEFORE_SKIP = 3   # consecutive RSS failures (any channel) before we stop asking for a while
RSS_SKIP_SECONDS = 1800        # ...and go straight to the channel-page fallback


async def poll_channel(conn: sqlite3.Connection, http: OutboundClient, channel_id: str,
                       lane: Lane = Lane.BACKGROUND) -> int:
    """Fetch and store one channel's RSS feed. Returns the count of new videos."""
    resp = await http.get(feed_url(channel_id), lane=lane)
    if resp.status_code != 200:
        raise ExtractionError(f"RSS {channel_id}: HTTP {resp.status_code}")
    feed = parse_feed(resp.text)
    feed.channel_id = channel_id   # trust the channel we asked for, not what the feed reports
    return subs.store_feed(conn, feed)


async def poll_channel_fallback(conn: sqlite3.Connection, extractor: Extractor, channel_id: str) -> int:
    """Find new uploads via the channel's page (yt-dlp) when RSS is unavailable. Background lane:
    it yields to anything you click and counts against the daily budget."""
    videos = await extractor.channel_videos(channel_id, 15, background=True)
    return subs.store_discovered(conn, channel_id, videos)


class Scheduler:
    def __init__(self, settings: Settings, conn: sqlite3.Connection, http: OutboundClient, *,
                 extractor: Extractor | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 rand: Callable[[], float] = random.random,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.settings, self.conn, self.http, self.extractor = settings, conn, http, extractor
        self._sleep, self._rand, self._clock = sleep, rand, clock
        self._rss_failures = 0
        self._rss_skip_until = 0.0

    async def _poll_rss(self, cid: str) -> int:
        try:
            n = await poll_channel(self.conn, self.http, cid)
        except DailyCapReached:
            raise
        except Exception:
            self._rss_failures += 1
            if self._rss_failures >= RSS_FAILURES_BEFORE_SKIP:
                self._rss_skip_until = self._clock() + RSS_SKIP_SECONDS
                log.warning("RSS has failed %d times in a row; using channel pages for %d min",
                            self._rss_failures, RSS_SKIP_SECONDS // 60)
            raise
        self._rss_failures = 0
        return n

    async def tick(self) -> str:
        """One unit of work. Returns 'polled', 'idle' or 'capped' (for tests/logging)."""
        due = subs.due_for_poll(self.conn, self.settings.rss_poll_interval)
        if not due:
            return "idle"
        cid = due[0]
        try:
            if self._clock() >= self._rss_skip_until:
                try:
                    log.info("rss %s: %d new", cid, await self._poll_rss(cid))
                    return "polled"
                except DailyCapReached:
                    raise
                except Exception as e:  # noqa: BLE001
                    if self.extractor is None:
                        raise
                    log.warning("rss %s failed (%s); trying its channel page", cid, e)
            if self.extractor is None:
                raise ExtractionError("RSS is being skipped and there is no fallback")
            n = await poll_channel_fallback(self.conn, self.extractor, cid)
            log.info("channel page %s: %d new", cid, n)
        except DailyCapReached:
            return "capped"
        except Exception as e:  # noqa: BLE001 - one bad channel must not stop the loop
            log.warning("poll %s failed: %s", cid, e)
            # Retry in RETRY_AFTER seconds (not a full poll interval), but never hammer a broken channel.
            self.conn.execute("UPDATE channels SET rss_checked_at=? WHERE id=?",
                              (int(time.time()) - self.settings.rss_poll_interval + RETRY_AFTER, cid))
        return "polled"

    async def run(self) -> None:
        while True:
            result = await self.tick()
            # Busy: a few seconds (jittered) between channels. Idle/capped: check back in a bit.
            base = 5.0 if result == "polled" else 300.0
            await self._sleep(base * (0.5 + self._rand()))
