"""Slowly fill in the details (title, channel, duration, date...) of videos known only by id.

A Takeout import brings in thousands of video ids. Asking YouTube about all of them would be a burst of
requests, so this works through them a few at a time:

* one video per call, on the BACKGROUND lane (yields to anything you click; counts against its daily cap);
* at most ``backfill_daily_cap`` videos in any rolling 24 hours (0 turns the backfill off). The count is
  read from the local event log, so it survives restarts;
* most useful first: liked videos, then watch later, then videos you watched more than once, then
  everything else you watched (most recent first), then playlist-only videos;
* private/deleted videos are remembered and never asked for again; other failures are retried later
  with growing delays, and abandoned after ``MAX_ATTEMPTS``;
* if YouTube asks us to prove we are not a bot, the whole backfill pauses for hours.
"""

from __future__ import annotations

import logging
import sqlite3
import time

from ..db.queries.events import log_event
from ..extract.base import BotCheck, ExtractionError, Extractor, VideoUnavailable
from ..net.ratelimit import DailyCapReached
from ..services.videos import VideoService

log = logging.getLogger("newtube.backfill")

DAY = 86400
MAX_ATTEMPTS = 4
RETRY_DELAYS = (2 * 3600, 6 * 3600, 24 * 3600)      # after the 1st, 2nd, 3rd failure
BOT_CHECK_PAUSE = 6 * 3600

# Lowest tier number goes first. Within a tier, the most recent event first.
_QUEUE_SQL = """
WITH ranked AS (
    SELECT v.id AS id,
           CASE WHEN SUM(e.type = 'import_like') > 0 THEN 0
                WHEN SUM(e.type = 'import_watch_later') > 0 THEN 1
                WHEN SUM(e.type = 'import_watch') > 1 THEN 2
                WHEN SUM(e.type = 'import_watch') = 1 THEN 3
                ELSE 4 END AS tier,
           MAX(e.ts) AS last_ts
    FROM videos v
    JOIN events e ON e.video_id = v.id AND substr(e.type, 1, 7) = 'import_'
    LEFT JOIN backfill_state b ON b.video_id = v.id
    WHERE v.detail_fetched_at IS NULL AND v.from_import = 1
      AND (b.video_id IS NULL OR (b.status = 'retry' AND b.next_try_at <= ?))
    GROUP BY v.id
)
SELECT id FROM ranked ORDER BY tier, last_ts DESC, id LIMIT ?
"""


def next_ids(conn: sqlite3.Connection, now: int | None = None, limit: int = 1) -> list[str]:
    return [r[0] for r in conn.execute(_QUEUE_SQL, (now if now is not None else int(time.time()), limit))]


def used_last_day(conn: sqlite3.Connection, now: int | None = None) -> int:
    now = now if now is not None else int(time.time())
    return conn.execute("SELECT count(*) FROM events WHERE type='backfill' AND ts > ?", (now - DAY,)).fetchone()[0]


def progress(conn: sqlite3.Connection, cap: int, now: int | None = None) -> dict[str, int]:
    """Numbers for `newtube backfill status`."""
    now = now if now is not None else int(time.time())
    one = lambda sql, *a: conn.execute(sql, a).fetchone()[0]  # noqa: E731
    return {
        "waiting": len(next_ids(conn, now, limit=1_000_000)),
        "waiting_later": one("SELECT count(*) FROM backfill_state WHERE status='retry' AND next_try_at > ?", now),
        "done": one("SELECT count(*) FROM videos WHERE from_import=1 AND detail_fetched_at IS NOT NULL"),
        "gone": one("SELECT count(*) FROM backfill_state WHERE status='gone'"),
        "used_last_day": used_last_day(conn, now),
        "daily_cap": cap,
    }


class Backfiller:
    def __init__(self, conn: sqlite3.Connection, extractor: Extractor, daily_cap: int, *,
                 clock=time.time) -> None:
        self.conn, self.extractor, self.daily_cap, self._clock = conn, extractor, daily_cap, clock
        self._service = VideoService(conn, extractor)
        self._paused_until = 0.0

    async def step(self) -> str:
        """Fetch at most one video. Returns 'fetched', 'gone', 'retry', 'idle' (nothing to do),
        'capped' (today's budget is used) or 'paused' (backing off after a bot check)."""
        now = int(self._clock())
        if self.daily_cap <= 0:
            return "idle"
        if now < self._paused_until:
            return "paused"
        if used_last_day(self.conn, now) >= self.daily_cap:
            return "capped"
        ids = next_ids(self.conn, now)
        if not ids:
            return "idle"
        vid = ids[0]
        # Log the attempt BEFORE asking: a crash mid-request must still count against the budget.
        log_event(self.conn, "backfill", video_id=vid, ts=now)
        try:
            info = await self.extractor.video_info(vid, background=True)
        except DailyCapReached:
            return "capped"
        except BotCheck:
            self._paused_until = now + BOT_CHECK_PAUSE
            log.warning("YouTube asked for a bot check; backfill paused for %d h", BOT_CHECK_PAUSE // 3600)
            return "paused"
        except VideoUnavailable as e:
            self._mark(vid, "gone", 1, 0, str(e))
            return "gone"
        except ExtractionError as e:
            return self._failed(vid, now, str(e))
        except Exception as e:  # noqa: BLE001 - one odd video must not stop the backfill
            return self._failed(vid, now, f"{type(e).__name__}: {e}")
        self._service.persist(info)            # not cached: these blobs hold stream URLs nobody will use
        return "fetched"

    def _failed(self, vid: str, now: int, note: str) -> str:
        row = self.conn.execute("SELECT attempts FROM backfill_state WHERE video_id=?", (vid,)).fetchone()
        attempts = (row[0] if row else 0) + 1
        if attempts >= MAX_ATTEMPTS:
            self._mark(vid, "gone", attempts, 0, f"gave up after {attempts} attempts: {note}")
            return "gone"
        self._mark(vid, "retry", attempts, now + RETRY_DELAYS[attempts - 1], note)
        return "retry"

    def _mark(self, vid: str, status: str, attempts: int, next_try_at: int, note: str) -> None:
        self.conn.execute(
            "INSERT INTO backfill_state(video_id, status, attempts, next_try_at, note) VALUES (?,?,?,?,?)"
            " ON CONFLICT(video_id) DO UPDATE SET status=excluded.status, attempts=excluded.attempts,"
            "  next_try_at=excluded.next_try_at, note=excluded.note",
            (vid, status, attempts, next_try_at, note[:300]))
