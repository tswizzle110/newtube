"""Playback preferences (codec, maximum resolution)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from ..app import Context
from ..media.dash import CODECS
from ..services import playback
from .deps import get_ctx

router = APIRouter(prefix="/api")


@router.get("/settings/playback")
async def get_playback(ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    return {**playback.load(ctx.conn), "codecs": list(CODECS), "max_heights": list(playback.MAX_HEIGHTS)}


@router.put("/settings/playback")
async def put_playback(body: dict[str, Any] = Body(...), ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    body = {k: v for k, v in body.items() if k not in ("codecs", "max_heights")}
    try:
        return playback.save(ctx.conn, body)
    except playback.PlaybackSettingsError as e:
        raise HTTPException(400, str(e)) from e
