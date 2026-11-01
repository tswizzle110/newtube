"""Read-only comments, loaded lazily by the watch page."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..app import Context
from ..extract.base import BotCheck, ExtractionError
from ..services.comments import MAX_LIMIT, CommentsNotLoaded, RepliesUnavailable
from .deps import get_ctx, valid_video_id

router = APIRouter(prefix="/api")
COMMENT_ID = re.compile(r"^[A-Za-z0-9_.-]{10,128}$")


def _sort(sort: str) -> str:
    if sort not in ("top", "new"):
        raise HTTPException(400, "sort must be 'top' or 'new'")
    return sort


@router.get("/comments/{video_id}")
async def comments(video_id: str, sort: str = "top", limit: int = Query(20, ge=1, le=MAX_LIMIT),
                   ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    valid_video_id(video_id)
    try:
        return await ctx.comments.top(video_id, _sort(sort), limit)
    except ExtractionError as e:
        raise HTTPException(503 if isinstance(e, BotCheck) else 502, str(e)) from e


@router.get("/comments/{video_id}/replies/{comment_id}")
async def replies(video_id: str, comment_id: str, sort: str = "top",
                  ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    valid_video_id(video_id)
    if not COMMENT_ID.match(comment_id):
        raise HTTPException(400, "bad comment id")
    try:
        return await ctx.comments.replies(video_id, _sort(sort), comment_id)
    except CommentsNotLoaded as e:
        raise HTTPException(404, "load the comments first") from e
    except RepliesUnavailable as e:
        raise HTTPException(409, "Replies are only available for the first 10 comments") from e
    except ExtractionError as e:
        raise HTTPException(503 if isinstance(e, BotCheck) else 502, str(e)) from e
