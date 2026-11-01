"""Subscriptions and the chronological subscription feed."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..app import Context
from ..db.queries import subscriptions as subs
from ..extract.base import ExtractionError
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
            "resume_position": r["resume_position"], "completed": bool(r["completed"])}


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
    new = 0
    try:   # fill the feed right away so the new channel isn't empty until the next poll
        new = await poll_channel(ctx.conn, ctx.http, ch.id, lane=Lane.INTERACTIVE)
    except Exception:  # noqa: BLE001 - subscription succeeded; the scheduler will retry
        pass
    return {"id": ch.id, "title": ch.title, "created": created, "new_videos": new}


@router.delete("/subscriptions/{channel_id}")
async def remove_sub(channel_id: str, ctx: Context = Depends(get_ctx)) -> dict[str, bool]:
    valid_channel_id(channel_id)
    return {"removed": subs.unsubscribe(ctx.conn, channel_id)}


@router.get("/feed")
async def feed(before: int | None = None, limit: int = 60,
               ctx: Context = Depends(get_ctx)) -> list[dict[str, Any]]:
    rows = subs.get_feed(ctx.conn, limit=min(max(limit, 1), 120), before=before)
    return [_card(r) for r in rows]
