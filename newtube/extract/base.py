"""Extractor interface and the plain data types it returns.

The rest of the app only imports from here. ``ytdlp.py`` is the single
implementation today; a local Invidious Companion (or anything else) can be
swapped in by implementing the same ``Extractor`` protocol.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

# ---------------------------------------------------------------- errors


class ExtractionError(Exception):
    """Something went wrong talking to / parsing YouTube."""


class VideoUnavailable(ExtractionError):
    """Private, deleted, region-blocked, members-only, ..."""


class AgeRestricted(VideoUnavailable):
    """Needs a signed-in account (not supported in phase 1)."""


class BotCheck(ExtractionError):
    """YouTube asked us to prove we're not a bot. Back off; upgrade yt-dlp."""


# ---------------------------------------------------------------- types


@dataclass
class Format:
    format_id: str
    url: str
    ext: str
    vcodec: str | None          # None when audio-only
    acodec: str | None          # None when video-only
    height: int | None = None
    width: int | None = None
    fps: float | None = None
    tbr: float | None = None    # total bitrate, kbit/s
    filesize: int | None = None
    language: str | None = None
    note: str | None = None
    language_preference: int | None = None   # yt-dlp: 10 = the video's original audio, -1 = a dub
    dynamic_range: str | None = None         # 'SDR', 'HDR10', 'HLG', ...

    @property
    def has_video(self) -> bool:
        return bool(self.vcodec)

    @property
    def has_audio(self) -> bool:
        return bool(self.acodec)

    @property
    def is_drc(self) -> bool:
        """Dynamic-range-compressed duplicate of an audio track (louder, flatter). Never auto-picked."""
        return "drc" in self.format_id.lower() or "DRC" in (self.note or "")

    @property
    def is_muxed(self) -> bool:
        return self.has_video and self.has_audio


@dataclass
class Chapter:
    start: float
    title: str


@dataclass
class Caption:
    lang: str
    name: str
    url: str
    ext: str
    auto: bool = False


@dataclass
class VideoInfo:
    id: str
    title: str
    channel_id: str | None
    channel_title: str | None
    description: str
    duration: int | None
    published_at: int | None
    view_count: int | None
    category: str | None
    tags: list[str]
    thumbnail_url: str | None
    is_live: bool
    age_limit: int
    chapters: list[Chapter] = field(default_factory=list)
    formats: list[Format] = field(default_factory=list)
    captions: list[Caption] = field(default_factory=list)
    fetched_at: int = 0


@dataclass
class ChannelInfo:
    id: str
    title: str
    handle: str | None = None
    avatar_url: str | None = None
    description: str | None = None
    subscriber_count: int | None = None


@dataclass
class VideoSummary:
    """A video as it appears in a list (search results, channel tab, playlist): no formats."""
    id: str
    title: str
    channel_id: str | None = None
    channel_title: str | None = None
    duration: int | None = None
    view_count: int | None = None
    published_at: int | None = None
    is_live: bool = False


@dataclass
class ChannelSummary:
    id: str
    title: str
    handle: str | None = None
    description: str | None = None
    avatar_url: str | None = None
    subscriber_count: int | None = None


@dataclass
class PlaylistSummary:
    id: str
    title: str
    channel_title: str | None = None
    video_count: int | None = None
    thumbnail_url: str | None = None


SearchItem = VideoSummary | ChannelSummary | PlaylistSummary


@dataclass
class Comment:
    id: str
    text: str
    author: str
    parent: str | None = None          # None for a top-level comment, else the parent comment's id
    author_id: str | None = None
    author_thumbnail: str | None = None
    like_count: int | None = None
    timestamp: int | None = None       # YouTube only says "2 days ago", so this is an estimate
    time_text: str | None = None       # ...and this is what YouTube actually said
    is_pinned: bool = False
    is_hearted: bool = False           # liked by the video's creator
    is_uploader: bool = False
    is_verified: bool = False


@dataclass
class CommentPage:
    comments: list[Comment]
    total_estimate: int | None = None


class Extractor(Protocol):
    """Everything the app needs from YouTube. Methods are async; lane choice
    (interactive vs background) is made by the caller via the rate limiter."""

    async def video_info(self, video_id: str, *, background: bool = False) -> VideoInfo: ...

    async def resolve_channel(self, ref: str, *, background: bool = False) -> ChannelInfo:
        """``ref`` may be a UC... id, an @handle, or a youtube.com channel URL."""
        ...

    async def search(self, query: str, kind: str = "video", limit: int = 20, *,
                     background: bool = False) -> list[SearchItem]:
        """``kind`` is 'video', 'channel' or 'playlist'."""
        ...

    async def channel_videos(self, channel_id: str, limit: int = 30, *,
                             background: bool = False) -> list[VideoSummary]: ...

    async def playlist(self, playlist_id: str, limit: int = 100, *,
                       background: bool = False) -> list[VideoSummary]: ...

    async def related(self, video_id: str, limit: int = 25, *, background: bool = False) -> list[VideoSummary]:
        """Videos YouTube considers related (its Mix for this video), closest first, without the video itself."""
        ...

    async def channel_page(self, channel_id: str, limit: int = 30, *,
                           background: bool = False) -> tuple[ChannelInfo, list[VideoSummary]]:
        """Channel header + newest uploads from ONE request (the Videos tab: no view counts)."""
        ...

    async def recent_uploads(self, channel_id: str, limit: int = 100, *,
                             background: bool = False) -> list[VideoSummary]:
        """The newest ``limit`` uploads (one page, ~1 request), with view counts. Used to top up a stored
        upload list without re-reading it."""
        ...

    async def channel_uploads(self, channel_id: str, limit: int = 10_000,
                              progress: Callable[[int, int | None], None] | None = None, *,
                              background: bool = False) -> tuple[list[VideoSummary], int | None]:
        """Up to ``limit`` of the channel's uploads, newest first, WITH view counts (from its
        "uploads" playlist), plus the channel's total video count if known. This is what Popular
        and Oldest are built from: YouTube ignores sort parameters on the Videos tab. It can take
        a minute for a big channel, so ``progress(done, total_or_None)`` reports as pages arrive."""
        ...

    async def playlist_page(self, playlist_id: str, limit: int = 100, *,
                            background: bool = False) -> tuple[PlaylistSummary, list[VideoSummary]]: ...

    async def comments(self, video_id: str, sort: str = "top", limit: int = 20, *,
                       replies_for: int = 0, replies_per_thread: int = 10,
                       background: bool = False) -> CommentPage:
        """Top-level comments (``sort`` is 'top' or 'new'). With ``replies_for=k`` the replies of
        the first k top-level comments are included too. NOTE: yt-dlp can't page or target one
        thread, so cost grows with ``limit`` and ``replies_for``; a provider with real
        continuation tokens (e.g. Invidious Companion) could do better behind this same method."""
        ...

    # Later: related videos.
