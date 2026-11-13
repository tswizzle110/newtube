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
from ..db.queries.events import log_event
from ..extract.base import BotCheck, ExtractionError, Extractor
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
# The channel-page fallback is a full yt-dlp lookup, the kind of request that gets an address flagged as a bot. So:
FALLBACK_PER_HOUR = 6          # at most this many per hour, however many channels are due (a feed outage must not become a burst)
FALLBACK_REVISIT = 12 * 3600   # a channel that needed it (its feed is broken) is looked at again only this often


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
        self.related = RelatedService(conn, extractor, settings) if extractor is not None else None
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

    def _fallback_allowed(self) -> bool:
        """Counted in the database (the event log), so a restart does not hand back a fresh allowance."""
        used = self.conn.execute("SELECT count(*) FROM events WHERE type='channel_fallback' AND ts > ?",
                                 (int(time.time()) - 3600,)).fetchone()[0]
        return used < FALLBACK_PER_HOUR

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
            if not self._fallback_allowed():
                # RSS is down and this hour's channel-page lookups are used up: leave it for later rather than ask more.
                self.conn.execute("UPDATE channels SET rss_checked_at=? WHERE id=?",
                                  (int(time.time()) - self.settings.rss_poll_interval + RETRY_AFTER, cid))
                return "polled"
            log_event(self.conn, "channel_fallback", channel_id=cid)   # counted BEFORE asking: a lookup that fails still used YouTube's patience
            n = await poll_channel_fallback(self.conn, self.extractor, cid)
            log.info("channel page %s: %d new", cid, n)
            # Its feed is broken, so do not come back to its page every few hours: twice a day is plenty.
            self.conn.execute("UPDATE channels SET rss_checked_at=? WHERE id=?",
                              (int(time.time()) - self.settings.rss_poll_interval + FALLBACK_REVISIT, cid))
        except DailyCapReached:
            return "capped"
        except Exception as e:  # noqa: BLE001 - one bad channel must not stop the loop
            log.warning("poll %s failed: %s", cid, e)
            # Retry in RETRY_AFTER seconds (not a full poll interval), but never hammer a broken channel.
            self.conn.execute("UPDATE channels SET rss_checked_at=? WHERE id=?",
                              (int(time.time()) - self.settings.rss_poll_interval + RETRY_AFTER, cid))
        return "polled"

    async def picture_step(self) -> str:
        """Find the picture of one subscribed channel that has none, within ``channel_picture_daily_cap`` a day (counted in
        the event log, before asking, so a restart does not hand back a fresh allowance). 'pictured', 'capped', 'paused',
        'retry' or 'idle'."""
        cap = self.settings.channel_picture_daily_cap
        if self.extractor is None or cap <= 0:
            return "idle"
        used = self.conn.execute("SELECT count(*) FROM events WHERE type='avatar_lookup' AND ts > ?",
                                 (int(time.time()) - 86400,)).fetchone()[0]
        if used >= cap:
            return "capped"
        missing = subs.channels_missing_picture(self.conn, 1)
        if not missing:
            return "idle"
        cid = missing[0]
        log_event(self.conn, "avatar_lookup", channel_id=cid)
        try:
            subs.upsert_channel(self.conn, await self.extractor.resolve_channel(cid, background=True))
        except DailyCapReached:
            return "capped"
        except BotCheck:
            return "paused"
        except Exception as e:  # noqa: BLE001 - one channel must not stop the loop; it is not asked again for three days
            log.warning("picture of %s: %s", cid, e)
            return "retry"
        log.info("picture of %s found", cid)
        return "pictured"

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
            self.backfiller.daily_cap = self.settings.backfill_daily_cap       # (changed on the Settings page? applies now)
            result = await self.backfiller.step()
        if result in ("idle", "capped", "paused") and await self.embed_step() == "embedded":
            result = "embedded"            # (the backfill having nothing to do leaves time for this)
        if result in ("idle", "capped", "paused") and await self.picture_step() == "pictured":
            result = "pictured"            # (local and cheap, and you can see it: so before the related lists)
        if result in ("idle", "capped", "paused") and self.related is not None:
            if await self.related.expand_step(self.settings.related_daily_cap) == "fetched":
                result = "related"         # ...and so does this, within its own budget
        return result

    def pause_after(self, result: str) -> float:
        """Seconds (before jitter) to rest after a step. Work that asks YouTube questions is SPREAD over the whole day:
        a cap of 30 a day means one about every 48 minutes, not 30 in half an hour, because a burst is what looks like
        a bot. Local work (embeddings) and channel polling go at their own, faster pace."""
        def spread(cap: int) -> float:
            return min(43200.0, max(60.0, 86400.0 / cap)) if cap > 0 else 300.0
        s = self.settings
        return {"polled": 5.0, "embedded": 3.0, "error": 60.0,
                "fetched": spread(s.backfill_daily_cap), "gone": spread(s.backfill_daily_cap), "retry": spread(s.backfill_daily_cap),
                "related": spread(s.related_daily_cap), "pictured": spread(s.channel_picture_daily_cap)}.get(result, 300.0)

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
            await self._sleep(self.pause_after(result) * (0.5 + self._rand()))
