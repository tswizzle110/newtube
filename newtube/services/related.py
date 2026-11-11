"""Related videos: fetched from YouTube's Mix for a video, stored locally, served to the watch page and the recommender.

Two ways a list gets fetched:
* you open a video (interactive lane, once per ``TTL``), shown as "Up next";
* a slow background job fetches the lists of videos you like most, within ``related_daily_cap`` per 24 hours
  (counted from the event log), so the recommender can suggest videos from channels you have never opened.
Failures are remembered for an hour so a flaky answer is not asked for again on every page view."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from typing import Any

from ..db.queries.events import log_event
from ..db.queries.seen import SEEN_SQL
from ..db.queries import settings as saved
from ..extract.base import BotCheck, ExtractionError, Extractor, RelatedUnsupported, VideoSummary
from ..ml import store as ml_store
from ..net.ratelimit import DailyCapReached
from ..recommend.baseline import DAY, interest_rows

log = logging.getLogger("newtube.related")
TTL = 3 * DAY
FAILURE_RETRY = 3600
EMPTY_TTL = DAY            # YouTube really returned nothing: ask again after a day, not three
LIMIT = 25
MIX_GIVE_UP_AFTER = 3      # this many empty Mixes in a row (and none that worked): stop asking for Mixes for a while
MIX_PAUSE = 6 * 3600       # ... then try one again, in case YouTube changed back
MIX_UNSUPPORTED_PAUSE = 7 * DAY   # yt-dlp does not read Mixes at all: look again only after a week (or a yt-dlp update)
MIX_SKIP_KEY = "related_mix_skip_until"
LOCAL_CHANNEL = 8          # from the same channel (newest first) ...
LOCAL_SIMILAR = 12         # ... then the closest by title/topic among videos this server already knows


class RelatedService:
    def __init__(self, conn: sqlite3.Connection, extractor: Extractor) -> None:
        self.conn, self.extractor = conn, extractor
        self._inflight: dict[str, asyncio.Task[int]] = {}
        self._mix_misses = 0
        self._mix_skip_until = float(saved.get(conn, MIX_SKIP_KEY, 0) or 0)       # (remembered across restarts)

    # ---- reading --------------------------------------------------------------------------------
    def _row(self, video_id: str):
        return self.conn.execute("SELECT fetched_at, count, failed FROM related_fetched WHERE video_id=?", (video_id,)).fetchone()

    def fresh(self, video_id: str, now: int | None = None) -> bool:
        now = now if now is not None else int(time.time())
        row = self._row(video_id)
        if not row:
            return False
        return now - row["fetched_at"] < (FAILURE_RETRY if row["failed"] else EMPTY_TTL if not row["count"] else TTL)

    def cards(self, video_id: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            f"SELECT r.related_id AS id, v.title, v.channel_id, COALESCE(c.title, '') AS channel_title, v.published_at,"
            f" v.duration, v.view_count, p.position AS resume_position, p.completed AS completed, {SEEN_SQL} AS seen"
            f" FROM related_videos r JOIN videos v ON v.id = r.related_id AND v.title <> ''"
            f" LEFT JOIN channels c ON c.id = v.channel_id LEFT JOIN video_progress p ON p.video_id = r.related_id"
            f" WHERE r.video_id = ? ORDER BY r.rank LIMIT ?", (video_id, limit)).fetchall()
        return [{**dict(r), "completed": bool(r["completed"]), "seen": bool(r["seen"])} for r in rows]

    def local_cards(self, video_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """Suggestions that need no request to YouTube at all, for when its own list is not available: the channel's
        other videos this server already knows (newest first), then the videos closest in meaning (the embeddings used
        for "similar to what you watch"). Never contains the video itself."""
        select = (f"SELECT v.id AS id, v.title, v.channel_id, COALESCE(c.title, '') AS channel_title, v.published_at,"
                  f" v.duration, v.view_count, p.position AS resume_position, p.completed AS completed, {SEEN_SQL} AS seen"
                  f" FROM videos v LEFT JOIN channels c ON c.id = v.channel_id LEFT JOIN video_progress p ON p.video_id = v.id")
        me = self.conn.execute("SELECT channel_id FROM videos WHERE id=?", (video_id,)).fetchone()
        ids: list[str] = []
        if me and me["channel_id"]:
            ids += [r[0] for r in self.conn.execute(
                "SELECT id FROM videos WHERE channel_id=? AND id<>? AND title<>'' ORDER BY COALESCE(published_at, 0) DESC LIMIT ?",
                (me["channel_id"], video_id, LOCAL_CHANNEL))]
        emb = self.conn.execute("SELECT model FROM video_embeddings WHERE video_id=? LIMIT 1", (video_id,)).fetchone()
        if emb:
            vectors = ml_store.load_vectors(self.conn, emb["model"])
            mine = vectors.pop(video_id, None)
            if mine:
                titled = {r[0] for r in self.conn.execute("SELECT id FROM videos WHERE title<>''")}
                ranked = sorted(((ml_store.dot(mine, v), i) for i, v in vectors.items() if i in titled and i not in ids),
                                reverse=True)
                ids += [i for _s, i in ranked[:LOCAL_SIMILAR]]
        ids = ids[:limit]
        if not ids:
            return []
        rows = {r["id"]: r for r in self.conn.execute(f"{select} WHERE v.id IN ({','.join('?' * len(ids))})", ids)}
        return [{**dict(rows[i]), "completed": bool(rows[i]["completed"]), "seen": bool(rows[i]["seen"])}
                for i in ids if i in rows]

    async def lookup(self, video_id: str, limit: int = 20, *, background: bool = False,
                     force: bool = False) -> tuple[list[dict[str, Any]], str]:
        """The related list for a video, fetching it first if it is missing or stale, and WHY if there is none.
        Never raises for a failed fetch (the page works without suggestions). ``force`` ignores the one-hour memory of a
        failed attempt (the Try again button); it never overrides the bot gate. Status:
          ok      there are videos            empty   YouTube returned none for this video
          paused  YouTube asked for a bot check; nothing was asked           failed  the lookup did not work"""
        status = None
        row = self._row(video_id)
        stale = not self.fresh(video_id) or (force and row is not None and row["failed"])
        if stale:
            try:
                await self.fetch(video_id, background=background)
            except BotCheck as e:
                log.info("related %s: %s", video_id, e)
                status = "paused"
            except (DailyCapReached, ExtractionError) as e:
                log.info("related %s: %s", video_id, e)
                status = "failed"
        cards = self.cards(video_id, limit)
        if cards:
            return cards, "ok"
        if status:
            return cards, status
        row = self._row(video_id)
        return cards, "failed" if row is not None and row["failed"] else "empty"

    async def for_video(self, video_id: str, limit: int = 20, *, background: bool = False) -> list[dict[str, Any]]:
        return (await self.lookup(video_id, limit, background=background))[0]

    # ---- fetching -------------------------------------------------------------------------------
    async def fetch(self, video_id: str, *, background: bool = False) -> int:
        """Fetch and store the list (several callers at once share one request). Returns how many were stored."""
        task = self._inflight.get(video_id)
        if task is None:
            task = asyncio.create_task(self._fetch(video_id, background))
            self._inflight[video_id] = task
            task.add_done_callback(lambda _t: self._inflight.pop(video_id, None))
        return await task

    async def _fetch(self, video_id: str, background: bool) -> int:
        now = int(time.time())
        try:
            found = []
            if time.time() >= self._mix_skip_until:
                try:
                    found = await self.extractor.related(video_id, LIMIT, background=background)
                except RelatedUnsupported as e:
                    log.warning("related: %s; searching by title instead for a week", e)
                    self._skip_mix(MIX_UNSUPPORTED_PAUSE)
                    found = []
                if found:
                    self._mix_misses = 0
                else:
                    self._mix_misses += 1
                    if self._mix_misses >= MIX_GIVE_UP_AFTER:
                        log.warning("related: YouTube's Mix came back empty %d times; searching by title instead for %dh",
                                    self._mix_misses, MIX_PAUSE // 3600)
                        self._skip_mix(MIX_PAUSE)
            if not found:
                found = await self._by_title(video_id, background)
        except (DailyCapReached, BotCheck):
            raise            # (a bot check is NOT remembered as a failure of this video: the gate paces us, and the list must
                             #  be fetched the moment YouTube relents, not an hour later)
        except Exception:
            self.conn.execute("INSERT INTO related_fetched(video_id, fetched_at, count, failed) VALUES (?,?,0,1)"
                              " ON CONFLICT(video_id) DO UPDATE SET fetched_at=excluded.fetched_at, count=0, failed=1", (video_id, now))
            raise
        return self.store(video_id, found, now)

    def _skip_mix(self, seconds: float) -> None:
        self._mix_skip_until, self._mix_misses = time.time() + seconds, 0
        saved.put(self.conn, MIX_SKIP_KEY, self._mix_skip_until)

    async def _by_title(self, video_id: str, background: bool) -> list[VideoSummary]:
        """When YouTube's own Mix is not available: the results of searching for the video's title, which are
        videos on the same subject (and often the same channel). One ordinary search request, the kind you make
        yourself from the search box."""
        row = self.conn.execute("SELECT title FROM videos WHERE id=?", (video_id,)).fetchone()
        title = (row["title"] if row else "").strip()[:200]
        if not title:
            return []
        hits = await self.extractor.search(title, "video", LIMIT + 5, background=background)
        return [h for h in hits if isinstance(h, VideoSummary) and h.id != video_id][:LIMIT]

    def store(self, video_id: str, found: list[VideoSummary], now: int | None = None) -> int:
        now = now if now is not None else int(time.time())
        c = self.conn
        c.execute("BEGIN")
        try:
            c.execute("DELETE FROM related_videos WHERE video_id=?", (video_id,))
            for rank, v in enumerate(found, start=1):
                channel = v.channel_id if v.channel_id and v.channel_id.startswith("UC") else None
                if channel:
                    c.execute("INSERT OR IGNORE INTO channels(id, title, created_at) VALUES (?,?,?)",
                              (channel, v.channel_title or channel, now))
                c.execute("INSERT OR IGNORE INTO videos(id, channel_id, title, duration, view_count, first_seen_at)"
                          " VALUES (?,?,?,?,?,?)", (v.id, channel, v.title, v.duration, v.view_count, now))
                # a video we only knew by id (from an import) learns its title and channel here
                c.execute("UPDATE videos SET title = CASE WHEN title = '' THEN ? ELSE title END,"
                          " channel_id = COALESCE(channel_id, ?), duration = COALESCE(duration, ?),"
                          " view_count = COALESCE(view_count, ?) WHERE id = ?",
                          (v.title, channel, v.duration, v.view_count, v.id))
                c.execute("INSERT INTO related_videos(video_id, related_id, rank) VALUES (?,?,?)", (video_id, v.id, rank))
            c.execute("INSERT INTO related_fetched(video_id, fetched_at, count, failed) VALUES (?,?,?,0)"
                      " ON CONFLICT(video_id) DO UPDATE SET fetched_at=excluded.fetched_at, count=excluded.count, failed=0",
                      (video_id, now, len(found)))
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
        return len(found)

    # ---- the slow background job ----------------------------------------------------------------
    def used_last_day(self, now: int | None = None) -> int:
        now = now if now is not None else int(time.time())
        return self.conn.execute("SELECT count(*) FROM events WHERE type='related_fetch' AND ts > ?", (now - DAY,)).fetchone()[0]

    def next_seed(self, now: int) -> str | None:
        """The video whose related list is most worth having: most interesting to you, not fetched recently."""
        score: dict[str, float] = {}
        for vid, _ch, weight, ts in interest_rows(self.conn, now):
            age_days = max(0.0, (now - ts) / DAY)
            score[vid] = score.get(vid, 0.0) + weight * 0.5 ** (age_days / 90)
        for vid, _ in sorted(score.items(), key=lambda kv: -kv[1]):
            if not self.fresh(vid, now):
                return vid
        return None

    async def expand_step(self, daily_cap: int, now: int | None = None) -> str:
        """Fetch one related list for the recommender. 'fetched', 'idle', 'capped' or 'failed'."""
        now = now if now is not None else int(time.time())
        if daily_cap <= 0:
            return "idle"
        if self.used_last_day(now) >= daily_cap:
            return "capped"
        seed = self.next_seed(now)
        if seed is None:
            return "idle"
        log_event(self.conn, "related_fetch", video_id=seed, ts=now)         # counted BEFORE asking, so a crash still counts
        try:
            await self.fetch(seed, background=True)
        except DailyCapReached:
            return "capped"
        except BotCheck:
            log.warning("YouTube asked for a bot check; related lookups paused for this cycle")
            return "capped"
        except Exception as e:  # noqa: BLE001
            log.info("related %s failed: %s", seed, e)
            return "failed"
        return "fetched"
