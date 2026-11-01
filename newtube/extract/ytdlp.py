"""yt-dlp implementation of the Extractor protocol.

THIS is the file to look at when YouTube breaks things. Usually the fix is
just ``uv lock --upgrade-package yt-dlp && uv sync``; failing that, the
option tweaks live in ``_base_opts`` and the parsing in ``parse_video_info``.

Notes
* One logical call = one rate-limiter token, even though yt-dlp may make a few
  HTTP requests internally. The limiter's spacing is set with that in mind.
* We only ever hand yt-dlp URLs we build ourselves from validated ids, never
  user-supplied URLs.
* yt-dlp is blocking, so it runs in a worker thread.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import re
import time
from typing import Any

import yt_dlp

from ..config import Settings
from ..net.http import ytdlp_network_opts
from ..net.ratelimit import Lane, RateLimiter
from .base import (
    AgeRestricted, BotCheck, Caption, Chapter, ChannelInfo, ExtractionError, Format,
    VideoInfo, VideoUnavailable,
)

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
HANDLE_RE = re.compile(r"^@[A-Za-z0-9._-]{3,}$")


# ------------------------------------------------------------ pure parsing


def classify_error(message: str) -> ExtractionError:
    """Map a yt-dlp error message to one of our exception types."""
    m = message.lower()
    if "not a bot" in m or ("confirm you" in m and "bot" in m):
        return BotCheck(message)
    if "age" in m and ("restrict" in m or "confirm your age" in m or "inappropriate" in m):
        return AgeRestricted(message)
    if any(s in m for s in ("private video", "video unavailable", "has been removed",
                            "members-only", "not available", "terminated", "copyright")):
        return VideoUnavailable(message)
    return ExtractionError(message)


def _published_at(info: dict[str, Any]) -> int | None:
    if info.get("timestamp"):
        return int(info["timestamp"])
    d = info.get("upload_date")  # 'YYYYMMDD'
    if d and re.fullmatch(r"\d{8}", d):
        return int(dt.datetime.strptime(d, "%Y%m%d").replace(tzinfo=dt.timezone.utc).timestamp())
    return None


def parse_formats(raw: list[dict[str, Any]]) -> list[Format]:
    out: list[Format] = []
    for f in raw or []:
        url = f.get("url") or ""
        # Skip storyboards (mhtml) and manifest-only entries; we want direct https media.
        if not url.startswith("https://") or f.get("ext") == "mhtml":
            continue
        if f.get("protocol") not in (None, "https"):
            continue
        vcodec = f.get("vcodec")
        acodec = f.get("acodec")
        out.append(Format(
            format_id=str(f["format_id"]),
            url=url,
            ext=f.get("ext") or "",
            vcodec=None if vcodec in (None, "none") else vcodec,
            acodec=None if acodec in (None, "none") else acodec,
            height=f.get("height"),
            width=f.get("width"),
            fps=f.get("fps"),
            tbr=f.get("tbr"),
            filesize=f.get("filesize") or f.get("filesize_approx"),
            language=f.get("language"),
            note=f.get("format_note"),
            language_preference=f.get("language_preference"),
        ))
    return out


def parse_captions(info: dict[str, Any]) -> list[Caption]:
    caps: list[Caption] = []

    def pick(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
        by_ext = {e.get("ext"): e for e in entries if e.get("url")}
        for ext in ("vtt", "srv3", "ttml", "json3"):
            if ext in by_ext:
                return by_ext[ext]
        return next(iter(by_ext.values()), None)

    for lang, entries in (info.get("subtitles") or {}).items():
        e = pick(entries)
        if e:
            caps.append(Caption(lang, e.get("name") or lang, e["url"], e["ext"], auto=False))
    orig = info.get("language")
    for lang, entries in (info.get("automatic_captions") or {}).items():
        # Auto captions come with ~150 machine translations; keep only the original language.
        if lang.endswith("-orig") or (orig and lang == orig):
            e = pick(entries)
            if e:
                caps.append(Caption(lang, (e.get("name") or lang) + " (auto)", e["url"],
                                    e["ext"], auto=True))
    return caps


def parse_video_info(info: dict[str, Any], now: int | None = None) -> VideoInfo:
    return VideoInfo(
        id=info["id"],
        title=info.get("title") or "",
        channel_id=info.get("channel_id"),
        channel_title=info.get("channel") or info.get("uploader"),
        description=info.get("description") or "",
        duration=int(info["duration"]) if info.get("duration") else None,
        published_at=_published_at(info),
        view_count=info.get("view_count"),
        category=(info.get("categories") or [None])[0],
        tags=list(info.get("tags") or []),
        thumbnail_url=info.get("thumbnail"),
        is_live=bool(info.get("is_live")) or info.get("live_status") in ("is_live", "is_upcoming"),
        age_limit=int(info.get("age_limit") or 0),
        chapters=[Chapter(float(c["start_time"]), c.get("title") or "")
                  for c in (info.get("chapters") or [])],
        formats=parse_formats(info.get("formats") or []),
        captions=parse_captions(info),
        fetched_at=now if now is not None else int(time.time()),
    )


def normalize_channel_ref(ref: str) -> str:
    """Turn a UC id / @handle / channel URL into a youtube.com /videos URL we build ourselves."""
    ref = ref.strip()
    if CHANNEL_ID_RE.match(ref):
        return f"https://www.youtube.com/channel/{ref}/videos"
    if HANDLE_RE.match(ref):
        return f"https://www.youtube.com/{ref}/videos"
    m = re.match(r"^(?:https?://)?(?:www\.|m\.)?youtube\.com/"
                 r"(channel/UC[A-Za-z0-9_-]{22}|@[A-Za-z0-9._-]+|c/[^/?#]+|user/[^/?#]+)", ref)
    if m:
        return f"https://www.youtube.com/{m.group(1)}/videos"
    raise ExtractionError(f"don't understand channel reference: {ref!r}")


def parse_channel_info(info: dict[str, Any]) -> ChannelInfo:
    cid = info.get("channel_id") or info.get("uploader_id")
    if not cid or not CHANNEL_ID_RE.match(cid):
        raise ExtractionError("channel id not found in extractor output")
    avatar = None
    for t in info.get("thumbnails") or []:
        if t.get("id") == "avatar_uncropped" or (avatar is None and "avatar" in str(t.get("id"))):
            avatar = t.get("url")
    handle = info.get("uploader_id")
    return ChannelInfo(
        id=cid,
        title=info.get("channel") or info.get("uploader") or info.get("title") or cid,
        handle=handle if handle and handle.startswith("@") else None,
        avatar_url=avatar,
        description=info.get("description"),
    )


# ------------------------------------------------------------ implementation


class YtDlpExtractor:
    def __init__(self, settings: Settings, limiter: RateLimiter) -> None:
        self._settings = settings
        self._limiter = limiter

    def _base_opts(self) -> dict[str, Any]:
        s = self._settings
        runtime: dict[str, Any] = {"path": s.js_runtime_path} if s.js_runtime_path else {}
        return {
            **ytdlp_network_opts(s),
            "skip_download": True,
            "js_runtimes": {s.js_runtime: runtime},
            "remote_components": [],     # never fetch solver code from GitHub/npm at runtime
            "noplaylist": True,
        }

    async def _extract(self, url: str, extra: dict[str, Any], background: bool) -> dict[str, Any]:
        await self._limiter.acquire(Lane.BACKGROUND if background else Lane.INTERACTIVE)

        def work() -> dict[str, Any]:
            with yt_dlp.YoutubeDL({**self._base_opts(), **extra}) as ydl:
                return ydl.sanitize_info(ydl.extract_info(url, download=False))

        try:
            return await asyncio.to_thread(work)
        except yt_dlp.utils.DownloadError as e:
            raise classify_error(str(e)) from e

    async def raw_video_info(self, video_id: str, *, background: bool = False) -> dict[str, Any]:
        """The unparsed yt-dlp info dict. yt-dlp specific; used by selftest --dump."""
        if not VIDEO_ID_RE.match(video_id):
            raise ExtractionError(f"bad video id: {video_id!r}")
        return await self._extract(f"https://www.youtube.com/watch?v={video_id}", {}, background)

    async def video_info(self, video_id: str, *, background: bool = False) -> VideoInfo:
        return parse_video_info(await self.raw_video_info(video_id, background=background))

    async def resolve_channel(self, ref: str, *, background: bool = False) -> ChannelInfo:
        url = normalize_channel_ref(ref)
        info = await self._extract(
            url, {"extract_flat": "in_playlist", "playlistend": 1, "noplaylist": False}, background
        )
        return parse_channel_info(info)

    # Phase 2.
    async def search(self, *a: Any, **k: Any) -> Any:
        raise NotImplementedError("search arrives in phase 2")

    async def comments(self, *a: Any, **k: Any) -> Any:
        raise NotImplementedError("comments arrive in phase 2")
