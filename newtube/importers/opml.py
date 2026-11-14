"""OPML: the standard list-of-feeds file every feed reader and YouTube client understands. Subscriptions go out as one, and come
in from one, WITHOUT asking YouTube anything: an OPML line already carries the channel id and its name. (Pictures and uploads
fill in afterwards, slowly, in the background.)"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from xml.sax.saxutils import quoteattr

FEED = "https://www.youtube.com/feeds/videos.xml?channel_id="
CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
MAX_BYTES = 2 * 1024 * 1024
MAX_CHANNELS = 5000


class BadOpml(ValueError):
    """Not a file we will read: the message says why."""


def export_opml(channels: Iterable[tuple[str, str]]) -> str:
    """``channels``: (channel id, title) pairs."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<opml version="1.1">', "  <head><title>newtube subscriptions</title></head>",
             "  <body>", '    <outline text="YouTube Subscriptions" title="YouTube Subscriptions">']
    for cid, title in channels:
        lines.append(f"      <outline text={quoteattr(title)} title={quoteattr(title)} type=\"rss\" xmlUrl={quoteattr(FEED + cid)}/>")
    lines += ["    </outline>", "  </body>", "</opml>", ""]
    return "\n".join(lines)


def parse_opml(text: str) -> list[tuple[str, str]]:
    """(channel id, title) for every YouTube feed in the file, first occurrence only. Anything else in it is ignored."""
    if len(text.encode("utf-8", "replace")) > MAX_BYTES:
        raise BadOpml("that file is too large to be a list of subscriptions")
    if re.search(r"<!(DOCTYPE|ENTITY)", text, re.IGNORECASE):
        raise BadOpml("that file declares entities, which newtube does not accept")       # (the classic way to make a parser explode)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise BadOpml(f"that is not a readable OPML file ({e})") from e
    out: dict[str, str] = {}
    for node in root.iter("outline"):
        m = re.search(r"channel_id=(UC[A-Za-z0-9_-]{22})", node.get("xmlUrl") or "")
        if m and CHANNEL_ID.match(m.group(1)) and m.group(1) not in out:
            out[m.group(1)] = (node.get("title") or node.get("text") or m.group(1)).strip()[:200] or m.group(1)
            if len(out) > MAX_CHANNELS:
                raise BadOpml("that file lists too many channels")
    if not out:
        raise BadOpml("no YouTube channels were found in that file")
    return list(out.items())
