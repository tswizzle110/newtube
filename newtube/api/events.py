"""Batch endpoint for everything the frontend logs. Local SQLite only."""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import tracking
from ..app import Context
from ..db.queries.events import log_event
from .deps import CHANNEL_ID, VIDEO_ID, get_ctx

router = APIRouter(prefix="/api")

# Signals the frontend is allowed to log. Add new names here when you add new signals.
EVENT_TYPES = {
    "play", "pause", "seek", "speed_change", "quality_change", "fullscreen",
    "comments_opened", "comments_scrolled", "chapter_click", "description_expanded",
    "like", "unlike", "not_interested", "more_like_this", "dont_recommend_channel",
    "search", "search_click", "feed_refresh",
    "sb_skip", "sb_undo", "sb_manual_skip", "sb_vote", "sb_submit",
    "mark_watched", "queue_add", "queue_remove", "autoplay_next", "autoplay_cancel", "pip", "audio_only",
    "player_error", "player_frozen", "player_refresh",       # (what the player had to recover from: for finding patterns)
}
SURFACES = {"for_you", "subs", "search", "related", "channel", "playlist", "history", "continue", "queue"}
MAX_PAYLOAD = 2048


class ImpressionIn(BaseModel):
    video_id: str
    surface: str
    position: int | None = None
    request_id: str | None = Field(None, max_length=64)
    visible_ms: int | None = Field(None, ge=0, le=3_600_000)
    ts: float | None = None            # when it happened, by the browser's clock (see `when`)


class ClickIn(BaseModel):
    video_id: str
    surface: str
    request_id: str | None = Field(None, max_length=64)
    position: int | None = None
    ts: float | None = None


class EventIn(BaseModel):
    type: str
    video_id: str | None = None
    channel_id: str | None = None
    session_id: str | None = Field(None, max_length=64)
    payload: dict[str, Any] | None = None
    ts: float | None = None


class WatchIn(BaseModel):
    session_id: str = Field(max_length=64)
    video_id: str
    source: str | None = Field(None, max_length=32)
    position: float = Field(ge=0)
    duration: float | None = Field(None, gt=0)
    intervals: list[list[float]] = Field(default_factory=list, max_length=2000)
    ended: bool = False
    started_at: float | None = None    # when this viewing began, by the browser's clock


class BatchIn(BaseModel):
    batch_id: str | None = Field(None, max_length=64)   # lets a re-sent batch be recognised and ignored
    sent_at: float | None = None                         # the browser's clock when this attempt was made
    impressions: list[ImpressionIn] = Field(default_factory=list, max_length=500)
    clicks: list[ClickIn] = Field(default_factory=list, max_length=100)
    events: list[EventIn] = Field(default_factory=list, max_length=500)
    watch: list[WatchIn] = Field(default_factory=list, max_length=20)


MAX_BACKDATE = 60 * 24 * 3600        # nothing the browser reports can be dated more than 60 days back


def when(ts: float | None, offset: float, now: int) -> int:
    """The server-side time of something the browser says happened at ``ts`` by ITS clock. ``offset`` (server clock
    minus browser clock, measured from this very request) corrects for a laptop whose clock is wrong. Never in the
    future, never absurdly old; unknown means "now"."""
    if ts is None:
        return now
    return int(min(now, max(now - MAX_BACKDATE, ts + offset)))


@router.post("/events")
async def ingest(batch: BatchIn, ctx: Context = Depends(get_ctx)) -> dict[str, int]:
    c = ctx.conn
    now = int(time.time())
    offset = (now - batch.sent_at) if batch.sent_at is not None else 0.0
    stored = {"impressions": 0, "clicks": 0, "events": 0, "watch": 0, "rejected": 0, "duplicate": 0}
    c.execute("BEGIN")
    try:
        if batch.batch_id:
            if c.execute("SELECT 1 FROM event_batches WHERE id=?", (batch.batch_id,)).fetchone():
                c.execute("ROLLBACK")
                stored["duplicate"] = 1                  # already recorded: say so, change nothing
                return stored
            c.execute("INSERT INTO event_batches(id, received_at) VALUES (?,?)", (batch.batch_id, now))
        for i in batch.impressions:
            if not VIDEO_ID.match(i.video_id) or i.surface not in SURFACES:
                stored["rejected"] += 1
                continue
            tracking.record_impression(c, video_id=i.video_id, surface=i.surface,
                                       position=i.position, request_id=i.request_id,
                                       visible_ms=i.visible_ms, now=when(i.ts, offset, now))
            stored["impressions"] += 1
        for k in batch.clicks:
            if not VIDEO_ID.match(k.video_id) or k.surface not in SURFACES:
                stored["rejected"] += 1
                continue
            if k.request_id:
                c.execute("UPDATE impressions SET clicked=1 WHERE request_id=? AND video_id=?",
                          (k.request_id, k.video_id))
            log_event(c, "click", video_id=k.video_id, ts=when(k.ts, offset, now),
                      payload={"surface": k.surface, "position": k.position,
                               "request_id": k.request_id})
            stored["clicks"] += 1
        for e in batch.events:
            bad = (e.type not in EVENT_TYPES
                   or (e.video_id and not VIDEO_ID.match(e.video_id))
                   or (e.channel_id and not CHANNEL_ID.match(e.channel_id))
                   or (e.payload is not None and len(json.dumps(e.payload)) > MAX_PAYLOAD))
            if bad:
                stored["rejected"] += 1
                continue
            log_event(c, e.type, video_id=e.video_id, channel_id=e.channel_id,
                      session_id=e.session_id, payload=e.payload, ts=when(e.ts, offset, now))
            stored["events"] += 1
        for w in batch.watch:
            if not VIDEO_ID.match(w.video_id):
                stored["rejected"] += 1
                continue
            tracking.record_watch(c, session_id=w.session_id, video_id=w.video_id, source=w.source,
                                  position=w.position, duration=w.duration,
                                  intervals=w.intervals, ended=w.ended, now=now,
                                  started_at=when(w.started_at, offset, now) if w.started_at is not None else None)
            stored["watch"] += 1
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK")
        raise
    return stored
