"""Subscriptions and the chronological subscription feed."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from ..app import Context
from ..db.queries import subscriptions as subs
from ..db.queries.events import log_event
from ..extract.base import BotCheck, ChannelInfo, ExtractionError
from ..importers import opml
from ..jobs.scheduler import poll_channel
from ..net.ratelimit import Lane
from .deps import get_ctx, valid_channel_id

router = APIRouter(prefix="/api")


class SubscribeIn(BaseModel):
    ref: str   # UC... id, @handle or youtube.com channel URL


def _card(r: Any) -> dict[str, Any]:
    return {"id": r["id"], "title": r["title"], "channel_id": r["channel_id"],
            "channel_title": r["channel_title"], "published_at": r["published_at"],
            "duration": r["duration"], "view_count": r["view_count"],
            "resume_position": r["resume_position"], "completed": bool(r["completed"]),
            "seen": bool(r["seen"])}


@router.get("/subscriptions")
async def list_subs(ctx: Context = Depends(get_ctx)) -> list[dict[str, Any]]:
    return [{"id": r["id"], "title": r["title"], "handle": r["handle"],
             "has_avatar": bool(r["avatar_url"])} for r in subs.list_subscriptions(ctx.conn)]


@router.post("/subscriptions")
async def add_sub(body: SubscribeIn, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    try:
        ch = await ctx.extractor.resolve_channel(body.ref)
    except ExtractionError as e:
        raise HTTPException(400, str(e)) from e
    created = subs.subscribe(ctx.conn, ch)
    new, poll_error = 0, None
    try:   # fill the feed right away so the new channel isn't empty until the next poll
        new = await poll_channel(ctx.conn, ctx.http, ch.id, lane=Lane.INTERACTIVE)
    except Exception as e:  # noqa: BLE001 - subscription itself succeeded; report why the feed is empty
        poll_error = f"{type(e).__name__}: {e}"
    return {"id": ch.id, "title": ch.title, "created": created, "new_videos": new,
            "poll_error": poll_error}


class OpmlIn(BaseModel):
    opml: str


@router.get("/subscriptions/export.opml")
async def export_subscriptions(ctx: Context = Depends(get_ctx)) -> Response:
    """Your subscriptions as an OPML file (the standard list every feed reader and YouTube client can import)."""
    text = opml.export_opml((r["id"], r["title"]) for r in subs.list_subscriptions(ctx.conn))
    return Response(text, media_type="text/x-opml", headers={"Content-Disposition": 'attachment; filename="newtube-subscriptions.opml"'})


@router.post("/subscriptions/import")
async def import_subscriptions(body: OpmlIn, ctx: Context = Depends(get_ctx)) -> dict[str, int]:
    """Subscribe to every channel in an OPML file. Nothing is asked of YouTube: the file has each channel's id and name, and
    the pictures and uploads fill in afterwards in the background."""
    try:
        channels = opml.parse_opml(body.opml)
    except opml.BadOpml as e:
        raise HTTPException(400, str(e)) from e
    added = sum(subs.subscribe(ctx.conn, ChannelInfo(cid, title), source="opml") for cid, title in channels)
    return {"found": len(channels), "added": added, "already": len(channels) - added}


@router.get("/subscriptions/new")
async def new_uploads(ctx: Context = Depends(get_ctx)) -> dict[str, int]:
    """How many new uploads are waiting (the number beside Subscriptions in the sidebar). Local only."""
    return {"count": subs.new_count(ctx.conn)}


@router.post("/subscriptions/seen")
async def seen(ctx: Context = Depends(get_ctx)) -> dict[str, bool]:
    """You opened Subscriptions: what is there now is no longer new."""
    subs.mark_seen(ctx.conn)
    return {"ok": True}


@router.post("/subscriptions/pictures")
async def fetch_pictures(limit: int = Query(15, ge=1, le=30), ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    """Find the pictures of subscriptions that have none, now, in a small batch (the background job does the same, slowly).
    One ordinary channel lookup each, one after another. Stops at once if YouTube asks for a bot check. A channel tried in the
    last few minutes is skipped, so pressing the button again moves on to the next ones instead of asking the same again."""
    todo = subs.channels_missing_picture(ctx.conn, limit, retry_after=300)
    found, stopped = 0, None
    for cid in todo:
        log_event(ctx.conn, "avatar_lookup", channel_id=cid)
        try:
            ch = await ctx.extractor.resolve_channel(cid)
        except BotCheck:
            stopped = "bot"
            break
        except ExtractionError:
            continue
        subs.upsert_channel(ctx.conn, ch)
        found += bool(ch.avatar_url)
    return {"tried": len(todo), "found": found, "stopped": stopped,
            "left": len(subs.channels_missing_picture(ctx.conn, 10_000, retry_after=300))}


@router.delete("/subscriptions/{channel_id}")
async def remove_sub(channel_id: str, ctx: Context = Depends(get_ctx)) -> dict[str, bool]:
    valid_channel_id(channel_id)
    return {"removed": subs.unsubscribe(ctx.conn, channel_id)}


@router.get("/feed")
async def feed(before: int | None = None, limit: int = 60,
               ctx: Context = Depends(get_ctx)) -> list[dict[str, Any]]:
    rows = subs.get_feed(ctx.conn, limit=min(max(limit, 1), 120), before=before)
    return [_card(r) for r in rows]
