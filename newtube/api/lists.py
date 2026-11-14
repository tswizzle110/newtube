"""Search, channel pages and playlists."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..app import Context
from ..db.queries import localsearch
from ..db.queries.events import log_event
from ..extract.base import BotCheck, ExtractionError
from ..extract.searchfilter import BadFilter, SearchFilter, check
from ..services.lists import enrich_videos
from .deps import get_ctx, valid_channel_id, valid_playlist_id

router = APIRouter(prefix="/api")


def _fail(e: ExtractionError) -> Exception:
    if isinstance(e, BotCheck):
        return e                                # re-raised as is: the app answers every bot check the same way
    return HTTPException(502, str(e))


@router.get("/search")
async def search(q: str = Query(min_length=1, max_length=200), kind: str = "video",
                 limit: int = Query(20, ge=1, le=50), date: str | None = None, duration: str | None = None,
                 sort: str | None = None, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    """``date`` (hour, today, week, month, year), ``duration`` (short, medium, long) and ``sort`` (date, views)
    narrow a VIDEO search; they are ignored for channels and playlists."""
    q = q.strip()
    if not q or kind not in ("video", "channel", "playlist"):
        raise HTTPException(400, "bad search")
    try:
        filters = check(SearchFilter(date or None, duration or None, sort or None))
    except BadFilter as e:
        raise HTTPException(400, str(e)) from e
    if kind != "video":
        filters = SearchFilter()
    try:
        results = await ctx.lists.search(q, kind, limit, filters or None)
    except ExtractionError as e:
        raise _fail(e) from e
    # Search history stays local; it is one of the recommender's taste signals.
    payload: dict[str, Any] = {"q": q, "kind": kind, "n": len(results)}
    if filters:
        payload["filters"] = {"date": filters.upload_date, "duration": filters.duration, "sort": filters.sort}
    log_event(ctx.conn, "search", payload=payload)
    return {"query": q, "kind": kind, "results": results}


@router.get("/search/suggest")
async def suggest(q: str = Query("", max_length=200), ctx: Context = Depends(get_ctx)) -> list[str]:
    """What you could search for: your own past searches. Local only (nothing is sent to YouTube while you type)."""
    return localsearch.suggestions(ctx.conn, q)


@router.get("/channel/{channel_id}/search")
async def channel_search(channel_id: str, q: str = Query(min_length=1, max_length=200), ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    """Search inside one channel, among the videos this server already has saved. No request to YouTube."""
    valid_channel_id(channel_id)
    videos, searched = localsearch.channel_videos(ctx.conn, channel_id, q)
    return {"videos": enrich_videos(ctx.conn, videos), "searched": searched}


@router.get("/channel/{channel_id}")
async def channel(channel_id: str, sort: str = "newest", limit: int = Query(30, ge=1, le=500),
                  offset: int = Query(0, ge=0, le=20_000), refresh: bool = False,
                  ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    valid_channel_id(channel_id)
    if sort not in ("newest", "oldest", "popular"):
        raise HTTPException(400, "sort must be newest, oldest or popular")
    try:
        return await ctx.lists.channel(channel_id, limit, sort, offset, refresh)
    except ExtractionError as e:
        raise _fail(e) from e


@router.get("/channel/{channel_id}/playlists")
async def channel_playlists(channel_id: str, ctx: Context = Depends(get_ctx)) -> list[dict[str, Any]]:
    """The playlists the channel has made."""
    valid_channel_id(channel_id)
    try:
        return await ctx.lists.channel_playlists(channel_id)
    except ExtractionError as e:
        raise _fail(e) from e


@router.get("/playlist/{playlist_id}")
async def playlist(playlist_id: str, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    valid_playlist_id(playlist_id)
    try:
        return await ctx.lists.playlist(playlist_id)
    except ExtractionError as e:
        raise _fail(e) from e
