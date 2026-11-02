"""Submitting segments and voting. The ONLY code that tells SponsorBlock which video you mean.

* Off by default: the setting ``contribute`` must be on, checked here on the server (not just in the UI).
* The SponsorBlock "user ID" is random, generated once and kept in the local database. It is
  never sent to the browser and is not linked to any account.
* We do NOT send SponsorBlock's optional "viewed" pings (they would report every single skip).
"""

from __future__ import annotations

import re
import secrets
import sqlite3
from typing import Any

from ..db.queries import settings as kv
from ..net.http import OutboundClient
from ..net.ratelimit import RateLimiter
from .models import CATEGORIES
from .provider import BASE
from .service import load_settings

USER_ID_KEY = "sponsorblock_user_id"
USER_AGENT = "newtube/0.1"
UUID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
VOTE_TYPES = {"up": 1, "down": 0, "undo": 20}
MIN_SEGMENT = 0.5


class ContributeError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


def explain(status: int, body: str) -> ContributeError:
    """Turn SponsorBlock's answer into something readable."""
    body = (body or "").strip()[:200]
    if status == 409:
        return ContributeError(409, "SponsorBlock says this was already submitted.")
    if status == 429:
        return ContributeError(429, "SponsorBlock is rate limiting you (too many submissions/votes); try again later.")
    if status == 403:
        return ContributeError(403, f"SponsorBlock refused it{': ' + body if body else ''}.")
    if status == 400:
        return ContributeError(400, f"SponsorBlock rejected it{': ' + body if body else ''}.")
    return ContributeError(502, f"SponsorBlock answered HTTP {status}.")


class Contributor:
    def __init__(self, conn: sqlite3.Connection, http: OutboundClient, limiter: RateLimiter) -> None:
        self.conn, self.http, self.limiter = conn, http, limiter

    def _require_enabled(self) -> None:
        if not load_settings(self.conn)["contribute"]:
            raise ContributeError(403, "Contributing is turned off. Enable it in Settings first.")

    def user_id(self) -> str:
        uid = kv.get(self.conn, USER_ID_KEY)
        if not uid:
            uid = secrets.token_hex(32)
            kv.put(self.conn, USER_ID_KEY, uid)
        return uid

    async def _post(self, path: str, body: dict[str, Any]) -> None:
        await self.limiter.acquire()
        try:
            r = await self.http.post(f"{BASE}{path}", json=body, limited=False)
        except Exception as e:  # noqa: BLE001
            raise ContributeError(502, f"Could not reach SponsorBlock ({type(e).__name__}).") from e
        if r.status_code != 200:
            raise explain(r.status_code, r.text)

    async def submit(self, video_id: str, start: float, end: float, category: str, action: str,
                     video_duration: float | None) -> None:
        self._require_enabled()
        if category not in CATEGORIES:
            raise ContributeError(400, "Unknown category.")
        if action not in ("skip", "mute", "poi"):
            raise ContributeError(400, "Unknown action.")
        if (action == "poi") != (category == "poi_highlight"):
            raise ContributeError(400, "Only the Highlight category is a single point in time.")
        if not video_duration or video_duration <= 0:
            raise ContributeError(502, "The video's length is unknown, so it can't be submitted.")
        if start < 0 or start > video_duration + 1:
            raise ContributeError(400, "Start time is outside the video.")
        if action == "poi":
            end = start
        elif end - start < MIN_SEGMENT or end > video_duration + 1:
            raise ContributeError(400, "The segment must be at least half a second long and end inside the video.")
        await self._post("/api/skipSegments", {
            "videoID": video_id, "userID": self.user_id(), "userAgent": USER_AGENT,
            "videoDuration": round(video_duration, 3),
            "segments": [{"segment": [round(start, 3), round(end, 3)], "category": category, "actionType": action}]})

    async def vote(self, uuid: str, vote: str) -> None:
        self._require_enabled()
        if vote not in VOTE_TYPES or not UUID_RE.match(uuid):
            raise ContributeError(400, "Bad vote.")
        await self._post("/api/voteOnSponsorTime", {"UUID": uuid, "userID": self.user_id(), "type": VOTE_TYPES[vote]})
