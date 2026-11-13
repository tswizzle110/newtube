"""Settings you can change from the Settings page instead of editing newtube.toml.

One description of every editable setting drives everything: the page (labels, help, limits), the validation (a bad
value is refused with a plain reason, never stored) and whether a change applies at once (`live`) or after a restart.

Where a value comes from, lowest priority first:  built-in default  <  newtube.toml  <  what you saved here
and an environment variable (NEWTUBE_<NAME>) beats everything: a setting fixed that way is shown as locked.
What you save here is kept in the database (``settings`` table, key ``app_settings``).

Not editable here on purpose: ``host``, ``port`` and the folders. Changing them from a page served on that very address
could cut you off with no way back in; they stay in the file.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .config import Settings, load_settings

KEY = "app_settings"
GROUPS = {
    "traffic": ("YouTube traffic", "How much newtube asks YouTube for in the background. Lower is quieter and safer from bot checks."),
    "network": ("Network and privacy", "Where requests go from, and a record of every outside connection."),
    "storage": ("Storage and backups", "Disk use and database backups."),
    "recs": ("Recommendations", "The model that judges what videos are about."),
}


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    help: str
    kind: str                       # 'int' | 'float' | 'choice' | 'text'
    group: str
    live: bool = True               # True: applies the moment you save. False: after newtube restarts.
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()
    scale: float = 1.0              # shown to you as value / scale (seconds stored, hours shown)
    unit: str = ""
    placeholder: str = ""


FIELDS: tuple[Field, ...] = (
    Field("backfill_daily_cap", "Imported videos looked up per day",
          "Fills in titles and channels for videos from your Google history, spread over the day (30 a day is one about every "
          "48 minutes). 0 turns it off.", "int", "traffic", minimum=0, maximum=2000),
    Field("related_daily_cap", "Related-video lookups per day",
          "Finds videos related to ones you like, for recommendations from channels you do not subscribe to. 0 turns it off.",
          "int", "traffic", minimum=0, maximum=500),
    Field("rss_poll_interval", "Check each channel for new videos every",
          "How often each subscribed channel's feed is checked. Longer is quieter; new uploads show up later.",
          "int", "traffic", minimum=900, maximum=172_800, scale=3600, unit="hours"),
    Field("min_request_interval", "Minimum pause between YouTube requests",
          "Seconds between requests, before the random extra below. Higher is gentler.", "float", "traffic",
          minimum=0.2, maximum=30, unit="seconds"),
    Field("request_jitter", "Random extra pause",
          "Up to this many extra seconds are added at random, so requests do not arrive on a rhythm.", "float", "traffic",
          minimum=0, maximum=30, unit="seconds"),
    Field("background_daily_cap", "Ceiling on all background requests per day",
          "A safety limit for everything newtube does on its own, feed checks included. About "
          "channels × 24 ÷ check-hours + 150 is plenty.", "int", "traffic", minimum=0, maximum=20_000),

    Field("outbound_proxy", "Send everything through a proxy",
          "For example http://127.0.0.1:8888 (a gluetun VPN). Empty means a direct connection. YouTube video links only work "
          "from the address that asked for them, so this carries the video data too.", "text", "network", live=False,
          placeholder="http://127.0.0.1:8888"),
    Field("ip_version", "Internet protocol towards YouTube",
          "auto uses whatever the system prefers. Choose 4 or 6 to force one (ignored when a proxy is set).",
          "choice", "network", live=False, choices=("auto", "4", "6")),
    Field("egress_mode", "Outside connections",
          "record notes every host newtube connects to (see `newtube privacy-check --log`). enforce also refuses anything off "
          "the allowlist. off does neither.", "choice", "network", live=False, choices=("record", "enforce", "off")),

    Field("cache_max_bytes", "Thumbnail cache size",
          "Thumbnails and channel pictures are kept on disk up to this size; the oldest are removed first.",
          "int", "storage", live=False, minimum=64 * 1024**2, maximum=2 * 1024**4, scale=1024**3, unit="GiB"),
    Field("backup_keep", "Database backups to keep",
          "`newtube backup` keeps this many of the newest copies.", "int", "storage", minimum=1, maximum=365),
    Field("backup_dir", "Backup folder",
          "Where `newtube backup` writes. Empty means the backups folder inside the data folder.", "text", "storage",
          placeholder="/mnt/other-disk/newtube-backups"),

    Field("related_strictness", "How closely Up next must match the video",
          "When YouTube's own list is not available, Up next searches for the video's title and keeps only results about the "
          "same subject. Strict shows fewer, closer ones; loose shows more, looser ones. A result from a channel you watch is "
          "judged one step more leniently. Applies to videos you open from now on.",
          "choice", "recs", choices=("loose", "normal", "strict")),
    Field("embedding_model", "Language model for \"similar to what you watch\"",
          "Empty uses the built-in simple method. Otherwise the name of a model folder under data/models/ (see the docs).",
          "text", "recs", live=False, placeholder="paraphrase-multilingual-MiniLM-L12-v2"),
    Field("embedding_device", "Run the model on",
          "auto uses the graphics card when there is one.", "choice", "recs", live=False, choices=("auto", "cuda", "cpu")),
)
BY_NAME = {f.name: f for f in FIELDS}

# One-click starting points. They only fill the form: nothing changes until you press Save.
PRESETS: dict[str, dict[str, Any]] = {
    "quiet": {"label": "Quiet profile", "help": "What one person's server can sustain without drawing attention.",
              "values": {"backfill_daily_cap": 30, "related_daily_cap": 10, "rss_poll_interval": 14400,
                         "min_request_interval": 2.0, "request_jitter": 2.0}},
    "paused": {"label": "Pause background lookups", "help": "Use while YouTube is asking for a bot check.",
               "values": {"backfill_daily_cap": 0, "related_daily_cap": 0}},
    "defaults": {"label": "Built-in defaults", "help": "The values newtube ships with.", "values": None},
}


class Invalid(ValueError):
    def __init__(self, name: str, message: str) -> None:
        super().__init__(message)
        self.name, self.message = name, message


def _proxy(value: str) -> str:
    if not value:
        return ""
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https", "socks4", "socks5", "socks5h") or not parts.hostname or re.search(r"\s", value):
        raise ValueError("a proxy address looks like http://127.0.0.1:8888 or socks5h://host:1080")
    return value


def validate(name: str, raw: Any) -> Any:
    """The stored value for ``raw``, or Invalid with a reason fit to show."""
    f = BY_NAME.get(name)
    if f is None:
        raise Invalid(name, f"{name} cannot be changed here")
    try:
        if f.kind in ("int", "float"):
            if isinstance(raw, bool) or raw is None or (isinstance(raw, str) and not raw.strip()):
                raise ValueError("enter a number")
            number = float(raw)
            if number != number or number in (float("inf"), float("-inf")):
                raise ValueError("enter a number")
            if f.kind == "int":
                if number != int(number):
                    raise ValueError("enter a whole number")
                number = int(number)
            lo, hi = f.minimum, f.maximum
            if (lo is not None and number < lo) or (hi is not None and number > hi):
                shown = lambda v: (f"{v / f.scale:g}" + (f" {f.unit}" if f.unit else ""))  # noqa: E731
                raise ValueError(f"must be between {shown(lo)} and {shown(hi)}")
            return number
        if f.kind == "choice":
            if raw not in f.choices:
                raise ValueError("choose one of: " + ", ".join(f.choices))
            return raw
        text = ("" if raw is None else str(raw)).strip()
        if len(text) > 300 or "\x00" in text:
            raise ValueError("too long or contains an invalid character")
        if name == "outbound_proxy":
            return _proxy(text)
        if name == "embedding_model" and not re.fullmatch(r"[A-Za-z0-9._-]{0,100}", text) or text in (".", ".."):
            raise ValueError("a folder name only (letters, digits, . _ -)")
        return text
    except ValueError as e:
        raise Invalid(name, f"{f.label}: {e}") from None


def locked_by_env(name: str, env: dict[str, str] | None = None) -> bool:
    return f"NEWTUBE_{name.upper()}" in (os.environ if env is None else env)


def load_overrides(conn: sqlite3.Connection) -> dict[str, Any]:
    """What you saved on the Settings page. Anything that no longer validates is ignored, never trusted."""
    try:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
        raw = json.loads(row[0]) if row else {}
    except (sqlite3.Error, ValueError, TypeError):
        return {}
    out: dict[str, Any] = {}
    for name, value in (raw.items() if isinstance(raw, dict) else ()):
        try:
            out[name] = validate(name, value)
        except Invalid:
            continue
    return out


def save_overrides(conn: sqlite3.Connection, overrides: dict[str, Any]) -> None:
    conn.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (KEY, json.dumps(overrides)))


def apply_db_overrides(settings: Settings, conn: sqlite3.Connection, env: dict[str, str] | None = None) -> dict[str, Any]:
    """Put what you saved on the page onto ``settings`` (not over an environment variable). Returns what was applied.
    Called at start-up by the server and by every command that reads settings, so they all agree."""
    applied = {}
    for name, value in load_overrides(conn).items():
        if not locked_by_env(name, env):
            setattr(settings, name, value)
            applied[name] = value
    return applied


def snapshot(settings: Settings) -> dict[str, Any]:
    return {f.name: getattr(settings, f.name) for f in FIELDS}


def file_value(name: str) -> Any:
    """The value newtube.toml / the environment / the default would give, ignoring anything saved on the page."""
    return getattr(load_settings(), name)
