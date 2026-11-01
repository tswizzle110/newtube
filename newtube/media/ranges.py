"""HTTP Range helpers for the video proxy."""

from __future__ import annotations

import re

# googlevideo throttles open-ended requests; yt-dlp avoids that by fetching ~10 MiB chunks.
# We do the same: the browser just sees ordinary 206 responses and asks for the next piece.
CHUNK = 10 * 1024 * 1024
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


def clamp_range(header: str | None, chunk: int = CHUNK) -> str:
    """Return the Range header to send upstream. Missing/open-ended/oversized ranges are
    clamped to ``chunk`` bytes. Unparseable or multi-range headers fall back to the first chunk."""
    if not header:
        return f"bytes=0-{chunk - 1}"
    m = _RANGE.match(header.strip())
    if not m or (m.group(1) == "" and m.group(2) == ""):
        return f"bytes=0-{chunk - 1}"
    start, end = m.group(1), m.group(2)
    if start == "":                       # suffix range "bytes=-500": pass through (tiny)
        return f"bytes=-{min(int(end), chunk)}"
    s = int(start)
    e = int(end) if end else s + chunk - 1
    return f"bytes={s}-{min(e, s + chunk - 1)}"
