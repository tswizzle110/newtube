"""Extractor interface and the plain data types it returns.

The rest of the app only imports from here. ``ytdlp.py`` is the single
implementation today; a local Invidious Companion (or anything else) can be
swapped in by implementing the same ``Extractor`` protocol.
"""

from __future__ import annotations

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

    @property
    def has_video(self) -> bool:
        return bool(self.vcodec)

    @property
    def has_audio(self) -> bool:
        return bool(self.acodec)

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


class Extractor(Protocol):
    """Everything the app needs from YouTube. Methods are async; lane choice
    (interactive vs background) is made by the caller via the rate limiter."""

    async def video_info(self, video_id: str, *, background: bool = False) -> VideoInfo: ...

    async def resolve_channel(self, ref: str, *, background: bool = False) -> ChannelInfo:
        """``ref`` may be a UC... id, an @handle, or a youtube.com channel URL."""
        ...

    # Phase 2: search, channel_videos, playlist, related, comments.
