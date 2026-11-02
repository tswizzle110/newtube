"""SponsorBlock: segments for a video, and the user's settings for acting on them."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from ..app import Context
from ..sponsorblock.models import CATEGORIES, LABELS
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
