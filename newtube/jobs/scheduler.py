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
from ..extract.base import ExtractionError
from ..extract.rss import feed_url, parse_feed
from ..net.http import OutboundClient
from ..net.ratelimit import DailyCapReached, Lane

log = logging.getLogger("newtube.jobs")
RETRY_AFTER = 600   # seconds before retrying a channel whose RSS poll failed


async def poll_channel(conn: sqlite3.Connection, http: OutboundClient, channel_id: str,
                       lane: Lane = Lane.BACKGROUND) -> int:
    """Fetch and store one channel's RSS feed. Returns the count of new videos."""
    resp = await http.get(feed_url(channel_id), lane=lane)
    if resp.status_code != 200:
        raise ExtractionError(f"RSS {channel_id}: HTTP {resp.status_code}")
    feed = parse_feed(resp.text)
    feed.channel_id = channel_id   # trust the channel we asked for, not what the feed reports
    return subs.store_feed(conn, feed)


class Scheduler:
    def __init__(self, settings: Settings, conn: sqlite3.Connection, http: OutboundClient, *,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 rand: Callable[[], float] = random.random) -> None:
        self.settings, self.conn, self.http = settings, conn, http
        self._sleep, self._rand = sleep, rand

    async def tick(self) -> str:
        """One unit of work. Returns 'polled', 'idle' or 'capped' (for tests/logging)."""
        due = subs.due_for_poll(self.conn, self.settings.rss_poll_interval)
        if not due:
            return "idle"
        cid = due[0]
        try:
            n = await poll_channel(self.conn, self.http, cid)
            log.info("rss %s: %d new", cid, n)
        except DailyCapReached:
            return "capped"
        except Exception as e:  # noqa: BLE001 - one bad channel must not stop the loop
            log.warning("rss %s failed: %s", cid, e)
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
