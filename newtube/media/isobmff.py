"""Locate the init segment and sidx index inside a fragmented MP4 (YouTube DASH).

A DASH-able MP4 looks like: ftyp, moov, sidx, then moof/mdat fragments. A player needs
two byte ranges: the *initialization* range (everything before the sidx) and the *index*
range (the sidx box itself). yt-dlp doesn't report them, so we read the first few KB of
each stream and walk the box headers.
"""

from __future__ import annotations

import struct
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


class NotDashMp4(Exception):
    """No sidx box found: not a stream a DASH player can index."""


@dataclass(frozen=True)
class Ranges:
    init_end: int      # initialization range is 0..init_end (inclusive)
    index_start: int
    index_end: int     # inclusive


@dataclass(frozen=True)
class NeedMore:
    bytes_needed: int  # total prefix length that would let us finish


def parse(data: bytes) -> Ranges | NeedMore:
    pos = 0
    while True:
        if pos + 8 > len(data):
            return NeedMore(pos + 16)
        size, typ = struct.unpack(">I4s", data[pos:pos + 8])
        if size == 1:                                   # 64-bit "largesize"
            if pos + 16 > len(data):
                return NeedMore(pos + 16)
            size = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
        elif size == 0:                                 # box runs to end of file
            raise NotDashMp4("box without size before sidx")
        if size < 8:
            raise NotDashMp4(f"corrupt box size {size}")
        if typ == b"sidx":
            if pos + size > len(data):
                return NeedMore(pos + size)
            return Ranges(init_end=pos - 1, index_start=pos, index_end=pos + size - 1)
        if typ in (b"moof", b"mdat"):                   # fragments before any sidx
            raise NotDashMp4("fragment data reached without a sidx")
        pos += size


async def probe_ranges(fetch: Callable[[int, int], Awaitable[bytes]], *,
                       first: int = 32 * 1024, cap: int = 4 * 1024 * 1024) -> Ranges:
    """``fetch(start, end_inclusive)`` returns those bytes. Usually one fetch is enough."""
    n = first
    while True:
        data = await fetch(0, n - 1)
        result = parse(data)
        if isinstance(result, Ranges):
            return result
        if len(data) < n or n >= cap:       # whole file read, or giving up
            raise NotDashMp4("no sidx within the first %d bytes" % len(data))
        n = min(cap, max(n * 2, result.bytes_needed))
