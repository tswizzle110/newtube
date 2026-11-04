"""Everything the browser fetches from YouTube goes through here (it never talks to Google)."""

from __future__ import annotations

import asyncio
import hashlib
import re
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from ..app import Context
from ..db.queries import cache
from ..extract.base import Format
from ..media import hls
from ..media.dash import CODECS, Rep, build_mpd, codec_family, select_formats
from ..media import isobmff, webm
from ..media.isobmff import NotDashMp4, Ranges
from ..media.webm import NotDashWebm
from ..services import playback
from ..media.ranges import CHUNK, clamp_range, parse_range, split_range
from ..net.http import HostNotAllowed, absolute_url, check_url
from .deps import get_ctx, valid_channel_id, valid_video_id

router = APIRouter(prefix="/api")

THUMB_NAMES = {"default", "mqdefault", "hqdefault", "sddefault", "maxresdefault", "best"}
# "best" = the sharpest picture the video has: 1280x720 when it exists, else 480x360, else the small 320x180 one.
BEST_ORDER = ("maxresdefault", "hqdefault", "mqdefault")
# For a size a video does not have, YouTube answers with a tiny grey placeholder: never keep one of those.
MIN_REAL_IMAGE = 2000
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
        r = None
        for size in (BEST_ORDER if name == "best" else (name,)):
            got = await ctx.http.get_media(f"https://i.ytimg.com/vi/{video_id}/{size}.jpg")
            if got.status_code == 200 and (name != "best" or len(got.content) >= MIN_REAL_IMAGE):
                r = got
                break
        if r is None:
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
        r = await ctx.http.get_media(absolute_url(row["avatar_url"]))
        if r.status_code != 200:
            raise HTTPException(404, "no avatar")
        body, ctype = r.content, r.headers.get("content-type", "image/jpeg")
        ctx.images.put(key, body, ctype)
    return Response(body, media_type=ctype, headers={"Cache-Control": "private, max-age=604800"})


IMAGE_HOSTS = ("ytimg.com", "ggpht.com", "googleusercontent.com")
MAX_IMAGE_BYTES = 3 * 1024 * 1024


@router.get("/img")
async def proxied_image(u: str, ctx: Context = Depends(get_ctx)) -> Response:
    """Fetch an image from YouTube's image hosts on the browser's behalf (channel avatars and
    playlist thumbnails of things you haven't subscribed to). Only those hosts are allowed."""
    url = absolute_url(u) or ""
    try:
        check_url(url)
    except HostNotAllowed as e:
        raise HTTPException(400, "host not allowed") from e
    host = (urlsplit(url).hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in IMAGE_HOSTS):
        raise HTTPException(400, "not an image host")
    key = "img:" + hashlib.sha256(url.encode()).hexdigest()
    hit = ctx.images.get(key)
    if hit:
        body, ctype = hit
    else:
        r = await ctx.http.get_media(url)
        ctype = r.headers.get("content-type", "")
        if r.status_code != 200 or not ctype.startswith("image/") or len(r.content) > MAX_IMAGE_BYTES:
            raise HTTPException(404, "no image")
        body = r.content
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


def _total_from(content_range: str | None) -> int | None:
    m = re.match(r"^bytes\s+\d+-\d+/(\d+)$", content_range or "")
    return int(m.group(1)) if m else None


@router.get("/stream/{video_id}/{format_id}")
async def stream(video_id: str, format_id: str, request: Request,
                 ctx: Context = Depends(get_ctx)) -> StreamingResponse:
    """Range-aware pass-through of one googlevideo format. Stream URLs expire, so on a
    403/404/410 we re-extract once and retry before giving up.

    Upstream requests are at most CHUNK bytes. An open-ended request ("bytes=N-") gets one chunk
    and the browser asks for the next. A BOUNDED request (a DASH player fetching one segment) gets
    exactly the bytes it asked for: if that is more than one chunk (a 4K segment can be 15 MB) we
    fetch several chunks and stream them back as one response."""
    valid_video_id(video_id)
    want_start, want_end = parse_range(request.headers.get("range"))
    upstream = None
    fmt = None
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
    assert upstream is not None and fmt is not None
    if upstream.status_code >= 400:
        code = upstream.status_code
        await upstream.aclose()
        raise HTTPException(502, f"upstream returned {code}")

    headers = {k: v for k, v in upstream.headers.items() if k.lower() in PASS_HEADERS}
    headers["Cache-Control"] = "private, no-store"
    total = _total_from(upstream.headers.get("content-range"))
    first_end = (want_start or 0) + CHUNK - 1
    more = (upstream.status_code == 206 and want_start is not None and want_end is not None
            and total is not None and min(want_end, total - 1) > first_end)
    pieces: list[tuple[int, int]] = []
    if more:
        last = min(want_end, total - 1)
        pieces = split_range(first_end + 1, last)
        headers["content-range"] = f"bytes {want_start}-{last}/{total}"
        headers["content-length"] = str(last - want_start + 1)

    async def body():
        resp = upstream
        try:
            async for chunk in resp.aiter_raw():
                yield chunk
            await resp.aclose()
            for a, b in pieces:                       # the rest of a big bounded range
                resp = await ctx.http.open_stream(fmt.url, headers={"Range": f"bytes={a}-{b}"})
                if resp.status_code != 206:
                    return                            # connection ends short: the player will notice
                async for chunk in resp.aiter_raw():
                    yield chunk
                await resp.aclose()
        finally:  # also runs when the browser seeks away and disconnects
            await resp.aclose()

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

    probe = webm.probe_ranges if fmt.ext == "webm" else isobmff.probe_ranges   # Cues vs sidx
    try:
        ranges = await probe(fetch)
    except (NotDashMp4, NotDashWebm, PermissionError):
        return None
    cache.put(ctx.conn, key, {"init_end": ranges.init_end, "index_start": ranges.index_start,
                              "index_end": ranges.index_end}, RANGES_TTL)
    return ranges


async def _reps(ctx: Context, video_id: str, lang: str | None, codec: str | None, *, avc_only: bool = False):
    """The video and audio streams to offer (with their byte layout), re-extracting once if the signed URLs went stale.
    Returns (info, video reps, audio reps), or None when no combination a player can use exists."""
    prefs = playback.load(ctx.conn)            # ?codec= overrides the preference (the player's fallback)
    for attempt in (0, 1):
        info = await ctx.videos.get(video_id, refresh=attempt == 1)
        video_fmts, audio_fmts = select_formats(info.formats, lang, "avc1" if avc_only else codec or prefs["codec"],
                                                prefs["max_height"])
        if avc_only:
            video_fmts = [f for f in video_fmts if codec_family(f) == "avc1"]
        results = await asyncio.gather(*(_ranges_for(ctx, video_id, f)
                                         for f in video_fmts + audio_fmts))
        reps = {f.format_id: Rep(f, r) for f, r in zip(video_fmts + audio_fmts, results) if r}
        video = [reps[f.format_id] for f in video_fmts if f.format_id in reps]
        audio = [reps[f.format_id] for f in audio_fmts if f.format_id in reps]
        if video and audio:
            return info, video, audio   # (a stale signed URL makes every probe fail; attempt 1 re-extracts once)
    return None


def _check_options(lang: str | None, codec: str | None = None) -> None:
    if lang is not None and not re.fullmatch(r"[A-Za-z0-9-]{1,16}", lang):
        raise HTTPException(400, "bad language")
    if codec is not None and codec not in CODECS:
        raise HTTPException(400, "bad codec")


@router.get("/dash/{video_id}.mpd")
async def dash_manifest(video_id: str, lang: str | None = None, codec: str | None = None,
                        ctx: Context = Depends(get_ctx)) -> Response:
    """DASH manifest for adaptive playback (up to 1080p H.264). 404 means: use the muxed fallback."""
    valid_video_id(video_id)
    _check_options(lang, codec)
    found = await _reps(ctx, video_id, lang, codec)
    if not found:
        raise HTTPException(404, "no DASH-compatible formats")
    info, video, audio = found
    mpd = build_mpd(video_id, info.duration or 0, video, audio)
    return Response(mpd, media_type="application/dash+xml", headers={"Cache-Control": "private, no-store"})


HLS_TYPE = "application/vnd.apple.mpegurl"


@router.get("/hls/{video_id}.m3u8")
async def hls_master(video_id: str, lang: str | None = None, ctx: Context = Depends(get_ctx)) -> Response:
    """HLS entry point for Apple TV / Safari / AirPlay (H.264 + AAC only). 404 means there is no such combination."""
    valid_video_id(video_id)
    _check_options(lang)
    found = await _reps(ctx, video_id, lang, None, avc_only=True)
    if not found:
        raise HTTPException(404, "no HLS-compatible formats")
    _info, video, audio = found
    a = audio[0].fmt
    text = hls.master_playlist(video_id, [r.fmt for r in video], a, a.language or "und")
    return Response(text, media_type=HLS_TYPE, headers={"Cache-Control": "private, no-store"})


@router.get("/hls/{video_id}/play")
async def hls_play(video_id: str) -> Response:
    """A bare page with one native <video> for the HLS stream: the way to try it (and AirPlay it) from Safari. Opening the
    .m3u8 address directly makes Safari hand the load to the system media viewer, which does not send the login cookie;
    a video element on a page does."""
    valid_video_id(video_id)
    page = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>newtube (HLS)</title><body style='margin:0;background:#000'>"
            f"<video src='/api/hls/{video_id}.m3u8' controls autoplay playsinline x-webkit-airplay=allow "
            "style='width:100vw;height:100vh'></video>")
    return Response(page, media_type="text/html", headers={"Cache-Control": "private, no-store"})


@router.get("/hls/{video_id}/{format_id}.m3u8")
async def hls_track(video_id: str, format_id: str, ctx: Context = Depends(get_ctx)) -> Response:
    """One track's fragment list, read from the stream's own sidx index (cached: it never changes for a stream)."""
    valid_video_id(video_id)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", format_id):
        raise HTTPException(400, "bad format")
    for attempt in (0, 1):
        info = await ctx.videos.get(video_id, refresh=attempt == 1)
        fmt = next((f for f in info.formats if f.format_id == format_id), None)
        if not fmt or fmt.ext not in ("mp4", "m4a"):       # (audio-only AAC streams are .m4a)
            raise HTTPException(404, "no such HLS track")
        ranges = await _ranges_for(ctx, video_id, fmt)
        segments = await _segments_for(ctx, video_id, fmt, ranges) if ranges else None
        if segments:
            break
    else:
        raise HTTPException(404, "track cannot be indexed")
    return Response(hls.media_playlist(video_id, fmt, ranges, segments), media_type=HLS_TYPE,
                    headers={"Cache-Control": "private, no-store"})


async def _segments_for(ctx: Context, video_id: str, fmt: Format, ranges: Ranges) -> list[hls.Segment] | None:
    key = f"sidx:{video_id}:{fmt.format_id}"
    hit = cache.get(ctx.conn, key)
    if hit:
        return [hls.Segment(*s) for s in hit]
    async with ctx.http.stream(fmt.url, headers={"Range": f"bytes={ranges.index_start}-{ranges.index_end}"}) as r:
        if r.status_code not in (200, 206):
            return None
        data = await r.aread()
    try:
        segments = hls.parse_sidx(data, ranges.index_start)
    except hls.BadSidx:
        return None
    cache.put(ctx.conn, key, [[s.start, s.size, s.seconds] for s in segments], RANGES_TTL)
    return segments
