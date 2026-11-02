"""Search, channel pages and playlists."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..app import Context
from ..db.queries.events import log_event
from ..extract.base import BotCheck, ExtractionError
from .deps import get_ctx, valid_channel_id, valid_playlist_id

router = APIRouter(prefix="/api")


def _fail(e: ExtractionError) -> HTTPException:
    return HTTPException(503 if isinstance(e, BotCheck) else 502, str(e))


@router.get("/search")
async def search(q: str = Query(min_length=1, max_length=200), kind: str = "video",
                 limit: int = Query(20, ge=1, le=50),
                 ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    q = q.strip()
    if not q or kind not in ("video", "channel", "playlist"):
        raise HTTPException(400, "bad search")
    try:
        results = await ctx.lists.search(q, kind, limit)
    except ExtractionError as e:
        raise _fail(e) from e
    # Search history stays local; it is one of the recommender's taste signals.
    log_event(ctx.conn, "search", payload={"q": q, "kind": kind, "n": len(results)})
    return {"query": q, "kind": kind, "results": results}


@router.get("/channel/{channel_id}")
async def channel(channel_id: str, sort: str = "newest", limit: int = Query(30, ge=1, le=120),
                  ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    valid_channel_id(channel_id)
    if sort not in ("newest", "oldest", "popular"):
        raise HTTPException(400, "sort must be newest, oldest or popular")
    try:
        return await ctx.lists.channel(channel_id, limit, sort)
    except ExtractionError as e:
        raise _fail(e) from e


@router.get("/playlist/{playlist_id}")
async def playlist(playlist_id: str, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    valid_playlist_id(playlist_id)
    try:
        return await ctx.lists.playlist(playlist_id)
    except ExtractionError as e:
        raise _fail(e) from e
