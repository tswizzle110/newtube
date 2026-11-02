"""Read a huge top-level JSON array one item at a time (Takeout's watch history can be 100+ MB)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any, TextIO

_WS = " \t\r\n﻿"


class NotAnArrayOfObjects(ValueError):
    """The file is JSON, but not a list of objects."""


def iter_json_array(fp: TextIO, chunk_size: int = 1 << 16) -> Iterator[Any]:
    """Yield each element of the top-level array, holding only a small window of the file in memory.

    Elements must be objects ({...}): a complete object is the only thing we can be sure is not cut off at
    the end of a chunk (a number or string could be). Raises NotAnArrayOfObjects for other content, and
    json.JSONDecodeError for damaged JSON (a missing comma, a truncated file...): a damaged export should
    be reported, not half-read in silence."""
    decoder = json.JSONDecoder()
    buf, i, eof = "", 0, False

    def fill() -> bool:
        nonlocal buf, i, eof
        if eof:
            return False
        data = fp.read(chunk_size)
        if not data:
            eof = True
            return False
        buf = buf[i:] + data               # drop what was already consumed
        i = 0
        return True

    def next_char() -> str | None:
        """Advance past whitespace; return the next significant character (None at end of file)."""
        nonlocal i
        while True:
            while i < len(buf) and buf[i] in _WS:
                i += 1
            if i < len(buf):
                return buf[i]
            if not fill():
                return None

    def damaged(msg: str) -> json.JSONDecodeError:
        return json.JSONDecodeError(msg, buf, i)

    if next_char() != "[":
        raise NotAnArrayOfObjects("not a JSON array")
    i += 1
    first = True
    while True:
        ch = next_char()
        if ch is None:
            raise damaged("unexpected end of file inside the array")
        if ch == "]" and first:
            return                                     # an empty array
        if not first:
            if ch == "]":
                return
            if ch != ",":
                raise damaged("expected ',' between items")
            i += 1
            ch = next_char()
            if ch is None:
                raise damaged("unexpected end of file inside the array")
            if ch == "]":
                raise damaged("trailing comma")
        if ch != "{":
            if first:
                raise NotAnArrayOfObjects("array elements are not objects")
            raise damaged("expected an object")
        while True:
            try:
                obj, end = decoder.raw_decode(buf, i)
            except json.JSONDecodeError:
                if not fill():                         # a real syntax error, not just a cut-off object
                    raise
                continue
            i = end
            break
        first = False
        yield obj
