"""Everything the browser fetches from YouTube goes through here (it never talks to Google)."""

from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from ..app import Context
from ..db.queries import cache
from ..extract.base import Format
from ..media.dash import Rep, build_mpd, select_formats
from ..media.isobmff import NotDashMp4, Ranges, probe_ranges
from ..media.ranges import clamp_range
from .deps import get_ctx, valid_channel_id, valid_video_id

router = APIRouter(prefix="/api")

THUMB_NAMES = {"default", "mqdefault", "hqdefault", "sddefault", "maxresdefault"}
PASS_HEADERS = ("content-type", "content-length", "content-range", "accept-ranges")


@router.get("/thumb/{video_id}/{name}")
async def thumbnail(video_id: str, name: str, ctx: Context = Depends(get_ctx)) -> Response:
    valid_video_id(video_id)
    if name not in THUMB_NAMES:
        raise HTTPException(400, "bad thumbnail name")
    key = f"thumb:{video_id}:{name}"
    hit = ctx.images.get(key)
    if hit:
        body, ctype = hit
    else:
        r = await ctx.http.get_media(f"https://i.ytimg.com/vi/{video_id}/{name}.jpg")
        if r.status_code != 200:
            raise HTTPException(404, "no thumbnail")
        body, ctype = r.content, r.headers.get("content-type", "image/jpeg")
        ctx.images.put(key, body, ctype)
    return Response(body, media_type=ctype, headers={"Cache-Control": "private, max-age=86400"})


@router.get("/avatar/{channel_id}")
async def avatar(channel_id: str, ctx: Context = Depends(get_ctx)) -> Response:
    valid_channel_id(channel_id)
    row = ctx.conn.execute("SELECT avatar_url FROM channels WHERE id=?", (channel_id,)).fetchone()
    if not row or not row["avatar_url"]:
        raise HTTPException(404, "no avatar")
    key = f"avatar:{channel_id}"
    hit = ctx.images.get(key)
    if hit:
        body, ctype = hit
    else:
        r = await ctx.http.get_media(row["avatar_url"])
        if r.status_code != 200:
            raise HTTPException(404, "no avatar")
        body, ctype = r.content, r.headers.get("content-type", "image/jpeg")
        ctx.images.put(key, body, ctype)
    return Response(body, media_type=ctype, headers={"Cache-Control": "private, max-age=604800"})


@router.get("/captions/{video_id}/{lang}.vtt")
async def captions(video_id: str, lang: str, ctx: Context = Depends(get_ctx)) -> Response:
    valid_video_id(video_id)
    info = await ctx.videos.get(video_id)
    cap = next((c for c in info.captions if c.lang == lang), None)
    if not cap:
        raise HTTPException(404, "no such caption track")
    if cap.ext != "vtt":
        raise HTTPException(415, f"caption format {cap.ext} not supported yet")
    r = await ctx.http.get(cap.url)
    if r.status_code != 200:
        raise HTTPException(502, "caption fetch failed")
    return Response(r.text, media_type="text/vtt", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/stream/{video_id}/{format_id}")
async def stream(video_id: str, format_id: str, request: Request,
                 ctx: Context = Depends(get_ctx)) -> StreamingResponse:
    """Range-aware pass-through of one googlevideo format. Stream URLs expire, so on a
    403/404/410 we re-extract once and retry before giving up."""
    valid_video_id(video_id)
    upstream = None
    for attempt in (0, 1):
        info = await ctx.videos.get(video_id, refresh=attempt == 1)
        fmt = next((f for f in info.formats if f.format_id == format_id), None)
        if not fmt:
            raise HTTPException(404, "no such format")
        upstream = await ctx.http.open_stream(
            fmt.url, headers={"Range": clamp_range(request.headers.get("range"))})
        if upstream.status_code in (403, 404, 410) and attempt == 0:
            await upstream.aclose()
            continue
        break
    assert upstream is not None
    if upstream.status_code >= 400:
        code = upstream.status_code
        await upstream.aclose()
        raise HTTPException(502, f"upstream returned {code}")

    async def body():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:  # also runs when the browser seeks away and disconnects
            await upstream.aclose()

    headers = {k: v for k, v in upstream.headers.items() if k.lower() in PASS_HEADERS}
    headers["Cache-Control"] = "private, no-store"
    return StreamingResponse(body(), status_code=upstream.status_code, headers=headers)


RANGES_TTL = 7 * 24 * 3600   # byte layout of a stream never changes, unlike its signed URL


async def _ranges_for(ctx: Context, video_id: str, fmt: Format) -> Ranges | None:
    key = f"ranges:{video_id}:{fmt.format_id}"
    hit = cache.get(ctx.conn, key)
    if hit:
        return Ranges(**hit)

    async def fetch(start: int, end: int) -> bytes:
        async with ctx.http.stream(fmt.url, headers={"Range": f"bytes={start}-{end}"}) as r:
            if r.status_code not in (200, 206):
                raise PermissionError(f"HTTP {r.status_code}")
            return await r.aread()

    try:
        ranges = await probe_ranges(fetch)
    except (NotDashMp4, PermissionError):
        return None
    cache.put(ctx.conn, key, {"init_end": ranges.init_end, "index_start": ranges.index_start,
                              "index_end": ranges.index_end}, RANGES_TTL)
    return ranges


@router.get("/dash/{video_id}.mpd")
async def dash_manifest(video_id: str, lang: str | None = None,
                        ctx: Context = Depends(get_ctx)) -> Response:
    """DASH manifest for adaptive playback (up to 1080p H.264). 404 means: use the muxed fallback."""
    valid_video_id(video_id)
    if lang is not None and not re.fullmatch(r"[A-Za-z0-9-]{1,16}", lang):
        raise HTTPException(400, "bad language")
    for attempt in (0, 1):
        info = await ctx.videos.get(video_id, refresh=attempt == 1)
        video_fmts, audio_fmts = select_formats(info.formats, lang)
        results = await asyncio.gather(*(_ranges_for(ctx, video_id, f)
                                         for f in video_fmts + audio_fmts))
        reps = {f.format_id: Rep(f, r) for f, r in zip(video_fmts + audio_fmts, results) if r}
        video = [reps[f.format_id] for f in video_fmts if f.format_id in reps]
        audio = [reps[f.format_id] for f in audio_fmts if f.format_id in reps]
        if video and audio:
            break   # a stale signed URL makes every probe fail; attempt 1 re-extracts once
    else:
        raise HTTPException(404, "no DASH-compatible formats")
    mpd = build_mpd(video_id, info.duration or 0, video, audio)
    return Response(mpd, media_type="application/dash+xml", headers={"Cache-Control": "private, no-store"})
