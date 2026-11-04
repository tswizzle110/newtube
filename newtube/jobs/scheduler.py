"""Background work, always through the BACKGROUND lane of the rate limiter.

Phase 1: poll subscribed channels' RSS feeds. Each tick handles at most one
channel, then sleeps a jittered interval, so polling is spread out instead of
bursty. When no channel is due, the same loop fills in details of imported (Takeout) videos, one at a
time, within its own daily budget (see backfill.py).
"""

from __future__ import annotations

import asyncio
import logging
import random
import sqlite3
import time
from collections.abc import Awaitable, Callable

from ..config import Settings
from ..db.queries import cache, subscriptions as subs
from ..extract.base import ExtractionError, Extractor
from ..extract.rss import feed_url, parse_feed
from ..net.http import OutboundClient
from ..net.ratelimit import DailyCapReached, Lane
from ..services.related import RelatedService
from ..ml.embedder import ModelNotInstalled, load_embedder
from ..ml.store import embed_pending_async
from ..net import audit
from .backfill import Backfiller

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
        self._last_purge = 0.0
        self.related = RelatedService(conn, extractor) if extractor is not None else None
        self._embedder = None
        self._embed_off = False
        self.backfiller = (Backfiller(conn, extractor, settings.backfill_daily_cap)
                           if extractor is not None else None)

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

    async def embed_step(self) -> str:
        """Embed a batch of videos for the similarity feature. Local work only; 'embedded' or 'idle'."""
        if self._embed_off:
            return "idle"
        try:
            if self._embedder is None:
                self._embedder = await asyncio.to_thread(load_embedder, self.settings)
            n = await embed_pending_async(self.conn, self._embedder)
        except ModelNotInstalled as e:
            self._embed_off = True
            log.warning("embeddings are off: %s", e)
            return "idle"
        except Exception as e:  # noqa: BLE001 - never let this take the poller down
            self._embed_off = True
            log.exception("embeddings stopped: %s", e)
            return "idle"
        return "embedded" if n else "idle"

    def flush_audit(self) -> None:
        a = audit.active()
        if a is not None:
            a.flush(self.conn)

    def housekeeping(self) -> int:
        """Delete expired cache rows (uploads lists can be megabytes): at most once an hour."""
        if self._clock() - self._last_purge < 3600:
            return 0
        self._last_purge = self._clock()
        self.conn.execute("DELETE FROM event_batches WHERE received_at < ?", (int(time.time()) - 14 * 24 * 3600,))
        return cache.purge_expired(self.conn)

    async def step(self) -> str:
        """One round of background work. Returns what happened, to pick the pause before the next."""
        self.housekeeping()
        self.flush_audit()
        result = await self.tick()
        if result == "idle" and self.backfiller is not None:
            result = await self.backfiller.step()
        if result in ("idle", "capped", "paused") and await self.embed_step() == "embedded":
            result = "embedded"            # (the backfill having nothing to do leaves time for this)
        if result in ("idle", "capped", "paused") and self.related is not None:
            if await self.related.expand_step(self.settings.related_daily_cap) == "fetched":
                result = "fetched"         # ...and so does this: one related list a minute, within its budget
        return result

    async def run(self) -> None:
        while True:
            try:
                result = await self.step()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                # Whatever went wrong (a locked database, a network oddity, a bug in one job), the loop must go on:
                # a dead loop would silently stop all feed polling until the next restart.
                log.exception("background step failed; carrying on in a minute")
                result = "error"
            # Busy: a few seconds (jittered) between channels. Backfill: about a minute between videos,
            # so the daily budget is spread over hours. Idle/capped: check back in a bit.
            base = {"polled": 5.0, "fetched": 60.0, "gone": 30.0, "retry": 60.0, "embedded": 3.0, "error": 60.0}.get(result, 300.0)
            await self._sleep(base * (0.5 + self._rand()))
