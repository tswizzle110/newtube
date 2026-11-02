"""Locate the init segment and the Cues index inside a WebM file (YouTube's VP9 DASH streams).

DASH needs two byte ranges: the *initialization* range (everything before the index: EBML header,
Segment header, SeekHead, Info, Tracks) and the *index* range (the ``Cues`` element). YouTube lays
its DASH WebM files out as ``EBML, Segment{ SeekHead, Info, Tracks, Cues, Cluster... }``, so the
Cues come before the first Cluster. yt-dlp doesn't report these ranges; we read the first few KB.

EBML in one paragraph: a file is a tree of elements ``[id][size][payload]``; ids and sizes are
variable-length integers whose first byte says how many bytes they use.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from .isobmff import NeedMore, Ranges

EBML_HEADER = 0x1A45DFA3
SEGMENT = 0x18538067
CUES = 0x1C53BB6B
CLUSTER = 0x1F43B675


class NotDashWebm(Exception):
    """No Cues before the first Cluster: not a stream a DASH player can index."""


def _read_id(data: bytes, pos: int) -> tuple[int, int] | None:
    """(id, length in bytes) or None when ``data`` ends too early. The marker bits stay in the id."""
    if pos >= len(data):
        return None
    first = data[pos]
    length = 1
    mask = 0x80
    while length <= 4 and not first & mask:
        length += 1
        mask >>= 1
    if length > 4:
        raise NotDashWebm("invalid element id")
    if pos + length > len(data):
        return None
    return int.from_bytes(data[pos:pos + length], "big"), length


def _read_size(data: bytes, pos: int) -> tuple[int | None, int] | None:
    """(size or None when 'unknown', length in bytes) or None when ``data`` ends too early."""
    if pos >= len(data):
        return None
    first = data[pos]
    length = 1
    mask = 0x80
    while length <= 8 and not first & mask:
        length += 1
        mask >>= 1
    if length > 8:
        raise NotDashWebm("invalid element size")
    if pos + length > len(data):
        return None
    value = first & (mask - 1)
    for b in data[pos + 1:pos + length]:
        value = (value << 8) | b
    unknown = value == (1 << (7 * length)) - 1
    return (None if unknown else value), length


def _header(data: bytes, pos: int) -> tuple[int, int | None, int] | None:
    """(element id, payload size or None, header length) at ``pos``, or None if truncated."""
    i = _read_id(data, pos)
    if i is None:
        return None
    s = _read_size(data, pos + i[1])
    if s is None:
        return None
    return i[0], s[0], i[1] + s[1]


def parse(data: bytes) -> Ranges | NeedMore:
    h = _header(data, 0)
    if h is None:
        return NeedMore(32)
    if h[0] != EBML_HEADER or h[1] is None:
        raise NotDashWebm("not a WebM/EBML file")
    pos = h[2] + h[1]
    seg = _header(data, pos)
    if seg is None:
        return NeedMore(pos + 32)
    if seg[0] != SEGMENT:
        raise NotDashWebm("no Segment after the EBML header")
    pos += seg[2]                                   # children of the Segment start here
    while True:
        child = _header(data, pos)
        if child is None:
            return NeedMore(pos + 32)
        cid, size, hlen = child
        if cid == CLUSTER:
            raise NotDashWebm("media data reached before any Cues")
        if size is None:
            raise NotDashWebm("element of unknown size before the Cues")
        end = pos + hlen + size
        if cid == CUES:
            if end > len(data):
                return NeedMore(end)
            return Ranges(init_end=pos - 1, index_start=pos, index_end=end - 1)
        pos = end


async def probe_ranges(fetch: Callable[[int, int], Awaitable[bytes]], *,
                       first: int = 64 * 1024, cap: int = 4 * 1024 * 1024) -> Ranges:
    """``fetch(start, end_inclusive)`` returns those bytes. Usually one fetch is enough."""
    n = first
    while True:
        data = await fetch(0, n - 1)
        result = parse(data)
        if isinstance(result, Ranges):
            return result
        if len(data) < n or n >= cap:
            raise NotDashWebm("no Cues within the first %d bytes" % len(data))
        n = min(cap, max(n * 2, result.bytes_needed))
