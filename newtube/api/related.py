"""Related videos for the watch page ("Up next"). Local after the first fetch."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..app import Context
from .deps import get_ctx, valid_video_id

router = APIRouter(prefix="/api")


@router.get("/related/{video_id}")
async def related(video_id: str, limit: int = 20, ctx: Context = Depends(get_ctx)) -> list[dict[str, Any]]:
    """Videos related to this one. A failed lookup is an empty list, not an error: the watch page works without it."""
    return await ctx.related.for_video(valid_video_id(video_id), min(max(limit, 1), 40))
