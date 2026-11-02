"""HTTP Range helpers for the video proxy."""

from __future__ import annotations

import re

# googlevideo throttles long single requests; yt-dlp avoids that by fetching ~10 MiB chunks. We do the
# same upstream. The browser, however, must get exactly the bytes it asked for, so a big explicit
# range is fetched as several chunks and stitched together (see api/media.py), never cut short.
CHUNK = 10 * 1024 * 1024
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


def parse_range(header: str | None) -> tuple[int | None, int | None]:
    """(start, end) of a single byte range; end is None when open-ended. A suffix range
    ("bytes=-500") is (None, 500). Missing, multi-range or malformed headers mean "from the start"."""
    m = _RANGE.match((header or "").strip())
    if not m or (m.group(1) == "" and m.group(2) == ""):
        return 0, None
    start, end = m.group(1), m.group(2)
    if start == "":
        return None, int(end)
    s = int(start)
    if end and int(end) < s:
        return 0, None
    return s, int(end) if end else None


def split_range(start: int, end: int, chunk: int = CHUNK) -> list[tuple[int, int]]:
    """Cut [start, end] (inclusive) into upstream-sized pieces."""
    return [(a, min(a + chunk - 1, end)) for a in range(start, end + 1, chunk)]


def clamp_range(header: str | None, chunk: int = CHUNK) -> str:
    """The Range to send upstream for the FIRST piece: open-ended/missing ranges are limited to one
    chunk (the browser just asks for the next piece); bounded ranges start at their own start."""
    start, end = parse_range(header)
    if start is None:                                  # suffix range: tiny, pass through
        return f"bytes=-{min(end or chunk, chunk)}"
    last = start + chunk - 1 if end is None else min(end, start + chunk - 1)
    return f"bytes={start}-{last}"
