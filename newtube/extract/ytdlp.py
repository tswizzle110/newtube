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
from urllib.parse import quote_plus
from typing import Any

import yt_dlp

from ..config import Settings
from ..net.http import absolute_url, ytdlp_network_opts
from ..net.ratelimit import Lane, RateLimiter
from .base import (
    AgeRestricted, BotCheck, Caption, Chapter, ChannelInfo, ChannelSummary, Comment, CommentPage,
    ExtractionError, Format, PlaylistSummary, SearchItem, VideoInfo, VideoSummary, VideoUnavailable,
)

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
HANDLE_RE = re.compile(r"^@[A-Za-z0-9._-]{3,}$")
PLAYLIST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{13,64}$")

# YouTube's `sp` search-filter parameter (type = video / channel / playlist).
SEARCH_FILTERS = {"video": "EgIQAQ%3D%3D", "channel": "EgIQAg%3D%3D", "playlist": "EgIQAw%3D%3D"}


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
        avatar_url=absolute_url(avatar),
        description=info.get("description"),
        subscriber_count=info.get("channel_follower_count"),
    )


def _last_thumb(e: dict[str, Any]) -> str | None:
    thumbs = [x for x in (e.get("thumbnails") or []) if x.get("url")]
    return absolute_url(thumbs[-1]["url"] if thumbs else e.get("thumbnail"))


def parse_entry(e: dict[str, Any]) -> SearchItem | None:
    """Turn one flat yt-dlp list entry into a summary. The kind is inferred from the id's shape
    (UC... channel, 11 chars video, longer playlist), not from the query, because result pages
    mix kinds. Returns None for anything else (shelves, ads, 'Shorts' rows)."""
    id_ = e.get("id") or ""
    title = e.get("title") or ""
    if CHANNEL_ID_RE.match(id_):
        handle = e.get("uploader_id") if str(e.get("uploader_id", "")).startswith("@") else None
        if handle is None:
            m = re.search(r"/(@[A-Za-z0-9._-]+)", e.get("url") or "")
            handle = m.group(1) if m else None
        return ChannelSummary(id_, title or e.get("channel") or id_, handle, e.get("description"),
                              _last_thumb(e), e.get("channel_follower_count"))
    if VIDEO_ID_RE.match(id_):
        pub = e.get("timestamp") or e.get("release_timestamp")
        return VideoSummary(
            id_, title, e.get("channel_id"), e.get("channel") or e.get("uploader"),
            int(e["duration"]) if e.get("duration") else None, e.get("view_count"),
            int(pub) if pub else None,
            e.get("live_status") in ("is_live", "is_upcoming") or bool(e.get("is_live")))
    if PLAYLIST_ID_RE.match(id_):
        return PlaylistSummary(id_, title, e.get("channel") or e.get("uploader"),
                               e.get("playlist_count") or e.get("n_entries"), _last_thumb(e))
    return None


def parse_entries(info: dict[str, Any]) -> list[SearchItem]:
    out: list[SearchItem] = []
    seen: set[str] = set()
    for e in info.get("entries") or []:
        item = parse_entry(e) if isinstance(e, dict) else None
        if item and item.id not in seen:
            seen.add(item.id)
            out.append(item)
    return out


def parse_comment(c: dict[str, Any]) -> Comment | None:
    """One comment from yt-dlp's ``info['comments']`` (field names as read from yt-dlp's source)."""
    if not c.get("id") or not isinstance(c.get("text"), str):
        return None
    parent = c.get("parent")
    return Comment(
        id=c["id"], text=c["text"], author=c.get("author") or "",
        parent=None if parent in (None, "root") else parent,
        author_id=c.get("author_id"), author_thumbnail=absolute_url(c.get("author_thumbnail")),
        like_count=c.get("like_count"),
        timestamp=int(c["timestamp"]) if c.get("timestamp") else None,
        time_text=c.get("_time_text") or None,
        is_pinned=bool(c.get("is_pinned")), is_hearted=bool(c.get("is_favorited")),
        is_uploader=bool(c.get("author_is_uploader")), is_verified=bool(c.get("author_is_verified")))


def parse_comments(info: dict[str, Any]) -> list[Comment]:
    seen: set[str] = set()
    out: list[Comment] = []
    for raw in info.get("comments") or []:
        c = parse_comment(raw) if isinstance(raw, dict) else None
        if c and c.id not in seen:
            seen.add(c.id)
            out.append(c)
    return out


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

    # ---- lists: search, channel uploads, playlists ----------------------------------------
    # Each has a raw_* twin returning yt-dlp's untouched dict (used by `selftest --dump`).

    @staticmethod
    def _flat(limit: int) -> dict[str, Any]:
        return {"extract_flat": "in_playlist", "playlistend": limit, "noplaylist": False}

    async def raw_search(self, query: str, kind: str = "video", limit: int = 20, *,
                         background: bool = False) -> dict[str, Any]:
        query = query.strip()
        if not 1 <= len(query) <= 200 or kind not in SEARCH_FILTERS:
            raise ExtractionError("bad search request")
        url = f"https://www.youtube.com/results?search_query={quote_plus(query)}&sp={SEARCH_FILTERS[kind]}"
        return await self._extract(url, self._flat(max(1, min(limit, 50))), background)

    async def search(self, query: str, kind: str = "video", limit: int = 20, *,
                     background: bool = False) -> list[SearchItem]:
        return parse_entries(await self.raw_search(query, kind, limit, background=background))

    async def raw_channel_videos(self, channel_id: str, limit: int = 30, *,
                                 background: bool = False) -> dict[str, Any]:
        if not CHANNEL_ID_RE.match(channel_id):
            raise ExtractionError(f"bad channel id: {channel_id!r}")
        return await self._extract(f"https://www.youtube.com/channel/{channel_id}/videos",
                                   self._flat(max(1, min(limit, 100))), background)

    async def channel_videos(self, channel_id: str, limit: int = 30, *,
                             background: bool = False) -> list[VideoSummary]:
        info = await self.raw_channel_videos(channel_id, limit, background=background)
        return [i for i in parse_entries(info) if isinstance(i, VideoSummary)]

    async def raw_playlist(self, playlist_id: str, limit: int = 100, *,
                           background: bool = False) -> dict[str, Any]:
        if not PLAYLIST_ID_RE.match(playlist_id):
            raise ExtractionError(f"bad playlist id: {playlist_id!r}")
        return await self._extract(f"https://www.youtube.com/playlist?list={playlist_id}",
                                   self._flat(max(1, min(limit, 200))), background)

    async def playlist(self, playlist_id: str, limit: int = 100, *,
                       background: bool = False) -> list[VideoSummary]:
        info = await self.raw_playlist(playlist_id, limit, background=background)
        return [i for i in parse_entries(info) if isinstance(i, VideoSummary)]

    async def channel_page(self, channel_id: str, limit: int = 30, *,
                           background: bool = False) -> tuple[ChannelInfo, list[VideoSummary]]:
        info = await self.raw_channel_videos(channel_id, limit, background=background)
        try:
            ch = parse_channel_info(info)
        except ExtractionError:     # header fields missing: still show the uploads
            ch = ChannelInfo(channel_id, info.get("channel") or info.get("title") or channel_id)
        ch.id = channel_id
        videos = [i for i in parse_entries(info) if isinstance(i, VideoSummary)]
        for v in videos:            # the channel tab doesn't repeat who uploaded each video
            v.channel_id, v.channel_title = v.channel_id or channel_id, v.channel_title or ch.title
        return ch, videos

    async def playlist_page(self, playlist_id: str, limit: int = 100, *,
                            background: bool = False) -> tuple[PlaylistSummary, list[VideoSummary]]:
        info = await self.raw_playlist(playlist_id, limit, background=background)
        videos = [i for i in parse_entries(info) if isinstance(i, VideoSummary)]
        head = PlaylistSummary(playlist_id, info.get("title") or "Playlist",
                               info.get("channel") or info.get("uploader"),
                               info.get("playlist_count") or len(videos), None)
        return head, videos

    # ---- comments ------------------------------------------------------------------------
    # yt-dlp reads comments as part of a full video extraction and can neither page nor target a
    # single thread: replies cost one extra request PER THREAD walked. So we (a) pace its internal
    # requests, (b) keep limits small, and (c) the caller caches what comes back.

    async def raw_comments(self, video_id: str, sort: str = "top", limit: int = 20, *,
                           replies_for: int = 0, replies_per_thread: int = 10,
                           background: bool = False) -> dict[str, Any]:
        if (not VIDEO_ID_RE.match(video_id) or sort not in ("top", "new")
                or not 1 <= limit <= 100 or not 0 <= replies_for <= 15
                or not 1 <= replies_per_thread <= 30):
            raise ExtractionError("bad comments request")
        if replies_for:     # first k threads, each with up to R replies (depth 2)
            parents, depth = min(replies_for, limit), "2"
            total = parents * (1 + replies_per_thread)
            per_thread = str(replies_per_thread)
        else:               # top level only: depth 1 means no reply requests at all
            parents, depth, total, per_thread = limit, "1", limit, "0"
        # yt-dlp's positional arg: max_comments=total,parents,replies,replies-per-thread,depth
        extra = {
            "getcomments": True,
            "extractor_args": {"youtube": {
                "max_comments": [str(total), str(parents), "all", per_thread, depth],
                "comment_sort": [sort]}},
            "sleep_interval_requests": self._settings.min_request_interval,
        }
        info = await self._extract(f"https://www.youtube.com/watch?v={video_id}", extra, background)
        return {"comments": info.get("comments") or [], "comment_count": info.get("comment_count")}

    async def comments(self, video_id: str, sort: str = "top", limit: int = 20, *,
                       replies_for: int = 0, replies_per_thread: int = 10,
                       background: bool = False) -> CommentPage:
        raw = await self.raw_comments(video_id, sort, limit, replies_for=replies_for,
                                      replies_per_thread=replies_per_thread, background=background)
        return CommentPage(parse_comments(raw), raw.get("comment_count"))
