"""Where segments come from.

``SegmentProvider`` is the seam: today ``SponsorBlockApi`` asks sponsor.ajay.app; a provider backed
by a local copy of the SponsorBlock database can replace it later without touching anything else.

Privacy: reads use the *hash-prefix* endpoint (the first 4 hex chars of sha256(video id)). The
server answers with every video sharing that prefix and we pick ours locally, so it never learns
which video we are watching. Only submitting/voting (contribute.py) reveals the video id.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol

from ..net.http import OutboundClient
from ..net.ratelimit import RateLimiter
from .models import CATEGORIES, Segment, SegmentSet

BASE = "https://sponsor.ajay.app"
PREFIX_LEN = 4          # the shortest the API accepts: the most anonymous
ACTION_TYPES = ["skip", "mute", "full", "poi"]


class SponsorBlockError(Exception):
    """The SponsorBlock server could not be reached or answered unexpectedly."""


class SegmentProvider(Protocol):
    async def segments(self, video_id: str) -> SegmentSet: ...


def hash_prefix(video_id: str) -> str:
    return hashlib.sha256(video_id.encode()).hexdigest()[:PREFIX_LEN]


def parse_response(data: Any, video_id: str) -> SegmentSet:
    """Pick OUR video out of a hash-prefix response and convert its segments."""
    full = hashlib.sha256(video_id.encode()).hexdigest()
    out = SegmentSet()
    if not isinstance(data, list):
        return out
    for item in data:
        if not isinstance(item, dict) or not (item.get("videoID") == video_id or item.get("hash") == full):
            continue
        for s in item.get("segments") or []:
            try:
                category, action = str(s["category"]), str(s.get("actionType") or "skip")
                start, end = float(s["segment"][0]), float(s["segment"][1])
            except (KeyError, TypeError, ValueError, IndexError):
                continue
            if category not in CATEGORIES:
                continue
            if action == "full":
                if category not in out.full_labels:
                    out.full_labels.append(category)
                continue
            if action not in ("skip", "mute", "poi") or end < start or start < 0:
                continue
            out.segments.append(Segment(
                uuid=str(s.get("UUID") or ""), start=start, end=end, category=category, action=action,
                votes=int(s.get("votes") or 0), locked=bool(s.get("locked")),
                video_duration=float(s.get("videoDuration") or 0), description=str(s.get("description") or "")))
    out.segments.sort(key=lambda g: (g.start, g.end))
    return out


class SponsorBlockApi:
    def __init__(self, http: OutboundClient, limiter: RateLimiter) -> None:
        self.http, self.limiter = http, limiter

    async def raw(self, video_id: str) -> Any:
        """The unparsed hash-prefix response ([] when nothing matches)."""
        await self.limiter.acquire()
        try:
            r = await self.http.get(
                f"{BASE}/api/skipSegments/{hash_prefix(video_id)}", limited=False,
                params={"categories": json.dumps(list(CATEGORIES)), "actionTypes": json.dumps(ACTION_TYPES)})
        except Exception as e:  # noqa: BLE001 - network problems are not fatal for playback
            raise SponsorBlockError(f"{type(e).__name__}: {e}") from e
        if r.status_code == 404:        # nobody has submitted anything for this prefix
            return []
        if r.status_code != 200:
            raise SponsorBlockError(f"HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError as e:
            raise SponsorBlockError("invalid JSON from SponsorBlock") from e

    async def segments(self, video_id: str) -> SegmentSet:
        return parse_response(await self.raw(video_id), video_id)
