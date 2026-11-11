"""Related videos for the watch page ("Up next"). Local after the first fetch."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..app import Context
from .deps import get_ctx, valid_video_id

router = APIRouter(prefix="/api")


@router.get("/related/{video_id}")
async def related(video_id: str, limit: int = 20, force: bool = False, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    """Videos related to this one, and WHY there are none when there are none (``status``: ok, empty, paused for a bot
    check, or failed; ``source`` says whether the videos came from YouTube or, when its list is not available, from what
    this server already knows: the same channel and similar titles). A failed lookup is never an error: the watch page works without it. ``force`` is the Try again
    button: it retries a failed lookup now (it still cannot get past a bot check)."""
    vid, limit = valid_video_id(video_id), min(max(limit, 1), 40)
    videos, status = await ctx.related.lookup(vid, limit, force=force)
    source = "youtube"
    if not videos:            # YouTube's list is not available: fill the page from what this server already knows
        videos = ctx.related.local_cards(vid, limit)
        source = "local" if videos else source
    return {"videos": videos, "status": status, "source": source}
