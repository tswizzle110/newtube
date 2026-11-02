"""Cached segment lookups and the user's SponsorBlock settings."""

from __future__ import annotations

import copy
import dataclasses
import re
import sqlite3
from typing import Any

from ..db.queries import cache, settings as kv
from .models import ACTIONS, CATEGORIES, DEFAULT_ACTIONS
from .provider import SegmentProvider, SponsorBlockError

CACHE_VERSION = 1
TTL = 6 * 3600       # segments change slowly; a negative result ("none yet") is cached too
SETTINGS_KEY = "sponsorblock"
CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")

DEFAULT_SETTINGS: dict[str, Any] = {
    "categories": dict(DEFAULT_ACTIONS),
    "min_length": 1.0,        # ignore segments shorter than this many seconds (not highlights)
    "show_toast": True,       # "Skipped ..." message with Undo
    "contribute": False,      # submitting/voting reveals the video id to SponsorBlock: opt-in
    "channels": {},           # per-channel overrides: {"UC...": {"sponsor": "ignore"}}
}


class SettingsError(ValueError):
    pass


def validate_settings(raw: dict[str, Any]) -> dict[str, Any]:
    """Return a clean settings dict or raise SettingsError. Unknown keys are rejected."""
    if not isinstance(raw, dict) or set(raw) - set(DEFAULT_SETTINGS):
        raise SettingsError("unknown settings keys")
    s = copy.deepcopy(DEFAULT_SETTINGS)
    if "categories" in raw:
        cats = raw["categories"]
        if not isinstance(cats, dict) or set(cats) - set(CATEGORIES) or any(v not in ACTIONS for v in cats.values()):
            raise SettingsError("bad categories")
        s["categories"].update(cats)
    if "min_length" in raw:
        if isinstance(raw["min_length"], bool) or not isinstance(raw["min_length"], (int, float)) \
                or not 0 <= raw["min_length"] <= 60:
            raise SettingsError("min_length must be 0-60 seconds")
        s["min_length"] = float(raw["min_length"])
    for flag in ("show_toast", "contribute"):
        if flag in raw:
            if not isinstance(raw[flag], bool):
                raise SettingsError(f"{flag} must be true or false")
            s[flag] = raw[flag]
    if "channels" in raw:
        ch = raw["channels"]
        if not isinstance(ch, dict) or len(ch) > 500:
            raise SettingsError("bad channel overrides")
        for cid, per in ch.items():
            if not CHANNEL_ID.match(cid) or not isinstance(per, dict) or set(per) - set(CATEGORIES) \
                    or any(v not in ACTIONS for v in per.values()):
                raise SettingsError(f"bad override for {cid!r}")
            if per:
                s["channels"][cid] = per
    return s


def load_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    stored = kv.get(conn, SETTINGS_KEY, {})
    try:
        return validate_settings(stored)
    except SettingsError:           # a hand-edited / outdated value must never break playback
        return copy.deepcopy(DEFAULT_SETTINGS)


def save_settings(conn: sqlite3.Connection, raw: dict[str, Any]) -> dict[str, Any]:
    clean = validate_settings(raw)
    kv.put(conn, SETTINGS_KEY, clean)
    return clean


class SponsorBlockService:
    def __init__(self, conn: sqlite3.Connection, provider: SegmentProvider) -> None:
        self.conn, self.provider = conn, provider

    async def get(self, video_id: str) -> dict[str, Any]:
        key = f"sb:v{CACHE_VERSION}:{video_id}"
        hit = cache.get(self.conn, key)
        if hit is not None:
            return hit
        try:
            found = await self.provider.segments(video_id)
        except SponsorBlockError as e:
            # Not cached: try again next time. Playback never depends on SponsorBlock being up.
            return {"segments": [], "full_labels": [], "error": str(e)}
        result = {"segments": [dataclasses.asdict(s) for s in found.segments],
                  "full_labels": found.full_labels, "error": None}
        cache.put(self.conn, key, result, TTL)
        return result
