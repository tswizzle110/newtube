"""Parse a channel's public RSS (Atom) feed. No account or API key needed.

URL: https://www.youtube.com/feeds/videos.xml?channel_id=UC...
The feed lists roughly the 15 newest uploads.
"""

from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .base import ExtractionError

NS = {
    "a": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


@dataclass
class FeedEntry:
    video_id: str
    title: str
    published_at: int
    thumbnail_url: str | None
    description: str | None
    view_count: int | None


@dataclass
class Feed:
    channel_id: str
    channel_title: str
    entries: list[FeedEntry]


def normalize_channel_id(raw: str) -> str:
    """YouTube's feed header reports the id WITHOUT the 'UC' prefix (22 chars); entries and
    the rest of the site use the full 24-char form. Always return the full form."""
    raw = raw.strip()
    return "UC" + raw if len(raw) == 22 else raw


def feed_url(channel_id: str) -> str:
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


def _ts(s: str) -> int:
    return int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def parse_feed(xml_text: str) -> Feed:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise ExtractionError(f"bad RSS XML: {e}") from e
    cid = root.findtext("yt:channelId", namespaces=NS)
    title = root.findtext("a:title", namespaces=NS)
    if not cid or not title:
        raise ExtractionError("RSS feed missing channel id/title")
    entries = []
    for e in root.findall("a:entry", NS):
        vid = e.findtext("yt:videoId", namespaces=NS)
        pub = e.findtext("a:published", namespaces=NS)
        if not vid or not pub:
            continue
        thumb = e.find("media:group/media:thumbnail", NS)
        views = e.find("media:group/media:community/media:statistics", NS)
        entries.append(FeedEntry(
            video_id=vid,
            title=e.findtext("a:title", default="", namespaces=NS),
            published_at=_ts(pub),
            thumbnail_url=thumb.get("url") if thumb is not None else None,
            description=e.findtext("media:group/media:description", namespaces=NS),
            view_count=int(views.get("views")) if views is not None and views.get("views") else None,
        ))
    return Feed(normalize_channel_id(cid), title, entries)
