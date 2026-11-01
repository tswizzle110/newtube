"""Everything the browser fetches from YouTube goes through here (it never talks to Google)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from ..app import Context
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
