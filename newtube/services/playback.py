"""Playback preferences: which video codec to prefer and the highest resolution to offer."""

from __future__ import annotations

import copy
import sqlite3
from typing import Any

from ..db.queries import settings as kv
from ..media.dash import CODECS

KEY = "playback"
MAX_HEIGHTS = (0, 720, 1080, 1440, 2160)        # 0 = no limit
DEFAULTS: dict[str, Any] = {
    # auto: the codec that reaches the highest resolution (VP9 for 1440p/4K, else H.264).
    "codec": "auto",
    "max_height": 0,
}


class PlaybackSettingsError(ValueError):
    pass


def validate(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) - set(DEFAULTS):
        raise PlaybackSettingsError("unknown settings keys")
    out = copy.deepcopy(DEFAULTS)
    if "codec" in raw:
        if raw["codec"] not in CODECS:
            raise PlaybackSettingsError(f"codec must be one of {', '.join(CODECS)}")
        out["codec"] = raw["codec"]
    if "max_height" in raw:
        if isinstance(raw["max_height"], bool) or raw["max_height"] not in MAX_HEIGHTS:
            raise PlaybackSettingsError(f"max_height must be one of {', '.join(map(str, MAX_HEIGHTS))}")
        out["max_height"] = int(raw["max_height"])
    return out


def load(conn: sqlite3.Connection) -> dict[str, Any]:
    try:
        return validate(kv.get(conn, KEY, {}))
    except PlaybackSettingsError:      # a hand-edited value must never break playback
        return copy.deepcopy(DEFAULTS)


def save(conn: sqlite3.Connection, raw: dict[str, Any]) -> dict[str, Any]:
    clean = validate(raw)
    kv.put(conn, KEY, clean)
    return clean
