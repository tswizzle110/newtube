"""Pure parsing of Google Takeout records. No I/O, no database: one record in, a result or a reason out.

Nothing here depends on the language of the export: file names, folder names and CSV headers are
localised in Takeout, so files are recognised by what their CONTENT looks like.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

VID = r"[A-Za-z0-9_-]{11}"
VIDEO_ID_RE = re.compile(rf"^{VID}$")
CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
PLAYLIST_ID_RE = re.compile(r"^(?:PL|LL|WL|FL|OL|UU|RD)[A-Za-z0-9_-]*$")
_WATCH = re.compile(rf"^https?://(?:www\.|m\.|music\.)?youtube\.com/(?:watch\?(?:[^#]*&)?v=|shorts/|live/)({VID})(?![A-Za-z0-9_-])")
_YOUTU_BE = re.compile(rf"^https?://youtu\.be/({VID})(?![A-Za-z0-9_-])")
_CHANNEL_IN_URL = re.compile(r"/channel/(UC[A-Za-z0-9_-]{22})")


# ---------------------------------------------------------------- results


@dataclass(frozen=True)
class WatchEvent:
    video_id: str
    ts: int
    title: str | None
    channel_id: str | None
    channel_title: str | None


@dataclass(frozen=True)
class SearchEvent:
    query: str
    ts: int


# Why an activity entry was dropped (reported, never silent).
SKIP_REASONS = {
    "ad": "ads", "removed": "removed videos", "unavailable": "deleted/private videos (title is just the URL)",
    "post": "community posts", "music": "YouTube Music", "no_url": "no link", "no_time": "no timestamp",
    "other_url": "other links (channels, playlists...)", "other_product": "other Google products", "malformed": "malformed",
}


# ---------------------------------------------------------------- small helpers


def parse_time(value: object) -> int | None:
    """ISO-8601 ('2024-05-01T12:34:56.789Z') to Unix seconds, or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        d = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return int(d.timestamp())


def video_id_from_url(url: str | None) -> str | None:
    if not url:
        return None
    m = _WATCH.match(url) or _YOUTU_BE.match(url)
    return m.group(1) if m else None


def channel_id_from_url(url: str | None) -> str | None:
    m = _CHANNEL_IN_URL.search(url or "")
    return m.group(1) if m else None


def search_query(url: str | None) -> str | None:
    if not url or "/results" not in url:
        return None
    q = parse_qs(urlsplit(url).query).get("search_query")
    return q[0].strip() or None if q else None


_TITLE_PREFIX = re.compile(r"^Watched\s+", re.IGNORECASE)      # English exports; other languages keep their prefix


# ---------------------------------------------------------------- activity (history) entries


def parse_activity(entry: object, include_music: bool = False) -> WatchEvent | SearchEvent | str:
    """One entry of watch-history.json / search-history.json: a WatchEvent, a SearchEvent, or the
    REASON (a key of SKIP_REASONS) it should be ignored."""
    if not isinstance(entry, dict):
        return "malformed"
    ts = parse_time(entry.get("time"))
    if ts is None:
        return "no_time"
    for d in entry.get("details") or []:                         # ads you were shown are logged as "watches"
        if isinstance(d, dict) and "google ads" in str(d.get("name", "")).lower():
            return "ad"
    header = str(entry.get("header") or "")
    if header and header not in ("YouTube", "YouTube Music"):
        return "other_product"
    url = entry.get("titleUrl") if isinstance(entry.get("titleUrl"), str) else None
    title = str(entry.get("title") or "")
    if not url:
        return "removed" if "removed" in title.lower() else "no_url"
    q = search_query(url)
    if q:
        return SearchEvent(q, ts)
    if header == "YouTube Music" or "music.youtube.com" in url:
        if not include_music or not video_id_from_url(url):
            return "music"
    if "/post/" in url:
        return "post"
    vid = video_id_from_url(url)
    if not vid:
        return "other_url"
    cleaned = _TITLE_PREFIX.sub("", title).strip()
    if cleaned.startswith(("http://", "https://")):              # a deleted/private video: Takeout shows its URL as the title
        return "unavailable"
    subs = entry.get("subtitles") or []
    first = subs[0] if subs and isinstance(subs[0], dict) else {}
    return WatchEvent(vid, ts, cleaned or None, channel_id_from_url(first.get("url")), (first.get("name") or None))


# ---------------------------------------------------------------- CSV files (subscriptions, playlists)


def _col(row: list[str], i: int) -> str:
    return row[i].strip() if i < len(row) else ""


def sniff_csv(rows: list[list[str]]) -> str:
    """'subscriptions' | 'playlist_items' | 'playlists' | 'unknown', from the shape of the DATA (not the
    header, which is localised). ``rows`` are the data rows, header excluded."""
    sample = [r for r in rows[:30] if any(c.strip() for c in r)]
    if not sample:
        return "unknown"

    def share(pred) -> float:                                     # type: ignore[no-untyped-def]
        return sum(1 for r in sample if pred(r)) / len(sample)

    if share(lambda r: CHANNEL_ID_RE.match(_col(r, 0)) is not None) >= 0.8:
        return "subscriptions"
    if share(lambda r: VIDEO_ID_RE.match(_col(r, 0)) is not None and parse_time(_col(r, 1)) is not None) >= 0.8:
        return "playlist_items"
    if share(lambda r: PLAYLIST_ID_RE.match(_col(r, 0)) is not None and parse_time(_col(r, 1)) is not None) >= 0.8:
        return "playlists"
    return "unknown"


def parse_subscriptions(rows: list[list[str]]) -> list[tuple[str, str]]:
    """[(channel_id, title)]. The title is the last column (id, url, title)."""
    out = []
    for r in rows:
        cid = _col(r, 0)
        if CHANNEL_ID_RE.match(cid):
            out.append((cid, (_col(r, len(r) - 1) if len(r) > 1 else "") or cid))
    return out


def parse_playlist_items(rows: list[list[str]]) -> list[tuple[str, int | None]]:
    """[(video_id, added_at)]."""
    return [(_col(r, 0), parse_time(_col(r, 1))) for r in rows if VIDEO_ID_RE.match(_col(r, 0))]


def parse_playlist_index(rows: list[list[str]]) -> list[tuple[str, str]]:
    """[(playlist_id, title)]: id, created, updated, [original title,] current title, visibility."""
    out = []
    for r in rows:
        pid = _col(r, 0)
        if PLAYLIST_ID_RE.match(pid):
            out.append((pid, _col(r, 4) or _col(r, 3) or pid))
    return out


# Words for "Liked videos" and "Watch later" in the languages YouTube is commonly used in. File names
# are localised, so the importer also accepts explicit names on the command line.
_LIKES = ("liked videos", "likes", "me gusta", "j'aime", "gefällt", "mi piace", "curtid", "gostei", "понравив", "高く評価", "좋아요")
_LATER = ("watch later", "ver más tarde", "ver mas tarde", "à regarder plus tard", "später ansehen", "guarda più tardi",
          "assistir mais tarde", "посмотреть позже", "後で見る", "나중에 볼 동영상")


def classify_playlist_name(name: str) -> str:
    n = re.sub(r"[-_]videos$", "", name.lower()).strip()      # "Liked videos-videos" -> "liked videos" (but keep "Liked videos")
    if any(w in n for w in _LATER):
        return "watch_later"
    if any(w in n for w in _LIKES):
        return "likes"
    return "other"
