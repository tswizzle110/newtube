"""SponsorBlock: segments for a video, and the user's settings for acting on them."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from ..app import Context
from ..db.queries import cache
from ..db.queries.events import log_event
from ..extract.base import ExtractionError
from ..sponsorblock.contribute import ContributeError
from ..sponsorblock.models import CATEGORIES, LABELS
from ..sponsorblock.service import CACHE_VERSION
from ..sponsorblock.service import SettingsError, load_settings, save_settings
from .deps import get_ctx, valid_video_id

router = APIRouter(prefix="/api")


@router.get("/sponsorblock/{video_id}")
async def segments(video_id: str, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    valid_video_id(video_id)
    return await ctx.sponsorblock.get(video_id)


@router.get("/settings/sponsorblock")
async def get_settings(ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    return {**load_settings(ctx.conn), "category_labels": LABELS, "category_order": list(CATEGORIES)}


@router.put("/settings/sponsorblock")
async def put_settings(body: dict[str, Any] = Body(...), ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    body = {k: v for k, v in body.items() if k not in ("category_labels", "category_order")}
    try:
        return save_settings(ctx.conn, body)
    except SettingsError as e:
        raise HTTPException(400, str(e)) from e


class SubmitIn(BaseModel):
    start: float = Field(ge=0, le=100_000)
    end: float = Field(ge=0, le=100_000)
    category: str = Field(max_length=32)
    action: str = Field("skip", max_length=8)


class VoteIn(BaseModel):
    uuid: str = Field(max_length=128)
    type: str = Field(max_length=8)          # 'up' | 'down' | 'undo'
    video_id: str | None = None             # only for OUR local log; SponsorBlock receives the UUID alone


@router.post("/sponsorblock/{video_id}/submit")
async def submit(video_id: str, body: SubmitIn, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    """Tells SponsorBlock which video this is: the one thing here that reveals it (see Settings)."""
    valid_video_id(video_id)
    try:
        duration = (await ctx.videos.get(video_id)).duration
    except ExtractionError as e:
        raise HTTPException(502, f"Could not read the video's length: {e}") from e
    try:
        await ctx.sb_contrib.submit(video_id, body.start, body.end, body.category, body.action, duration)
    except ContributeError as e:
        raise HTTPException(e.status, e.message) from e
    cache.delete(ctx.conn, f"sb:v{CACHE_VERSION}:{video_id}")      # so the next load can include it
    log_event(ctx.conn, "sb_submit", video_id=video_id,
              payload={"category": body.category, "start": body.start, "end": body.end})
    return {"ok": True}


@router.post("/sponsorblock/vote")
async def vote(body: VoteIn, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    try:
        await ctx.sb_contrib.vote(body.uuid, body.type)
    except ContributeError as e:
        raise HTTPException(e.status, e.message) from e
    log_event(ctx.conn, "sb_vote", video_id=body.video_id if body.video_id and len(body.video_id) == 11 else None,
              payload={"uuid": body.uuid, "type": body.type})
    return {"ok": True}
