"""YouTube's `sp` search parameter: a small protobuf message, base64-encoded, that carries the search filters.

    message SearchParams { int32 sort = 1; Filters filters = 2; }
    message Filters      { int32 upload_date = 1; int32 type = 2; int32 duration = 3; }

The numbers are the ones YouTube's own filter menu produces (verified against its published values, e.g.
"EgIQAQ%3D%3D" = videos only, "EgQQARgB" = videos under 4 minutes, "EgQIAhAB" = uploaded today, "CAI%3D" = sort by
upload date). Nothing here talks to YouTube: it only builds a string."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from urllib.parse import quote

KINDS = {"video": 1, "channel": 2, "playlist": 3}
UPLOAD_DATES = {"hour": 1, "today": 2, "week": 3, "month": 4, "year": 5}
DURATIONS = {"short": 1, "medium": 3, "long": 2}        # under 4 min, 4 to 20 min, over 20 min
SORTS = {"date": 2, "views": 3}                         # (relevance is the default: no sort at all)


class BadFilter(ValueError):
    pass


@dataclass(frozen=True)
class SearchFilter:
    upload_date: str | None = None
    duration: str | None = None
    sort: str | None = None

    def __bool__(self) -> bool:
        return bool(self.upload_date or self.duration or self.sort)

    def key(self) -> str:
        return f"{self.upload_date or '-'}:{self.duration or '-'}:{self.sort or '-'}"


def check(f: SearchFilter) -> SearchFilter:
    for value, allowed, name in ((f.upload_date, UPLOAD_DATES, "upload date"), (f.duration, DURATIONS, "duration"),
                                 (f.sort, SORTS, "sort")):
        if value is not None and value not in allowed:
            raise BadFilter(f"unknown {name}: {value!r}")
    return f


def _field(number: int, value: int) -> bytes:
    assert 0 <= value < 128 and 0 < number < 16            # small varints only: all we ever need
    return bytes([(number << 3) | 0, value])


def build_sp(kind: str, f: SearchFilter | None = None) -> str:
    """The `sp=` value (already URL-encoded) for a search of this kind with these filters. Upload date and
    duration only apply to videos; for channels and playlists they are ignored."""
    if kind not in KINDS:
        raise BadFilter(f"unknown kind: {kind!r}")
    f = check(f or SearchFilter())
    inner = b""
    if kind == "video" and f.upload_date:
        inner += _field(1, UPLOAD_DATES[f.upload_date])
    inner += _field(2, KINDS[kind])
    if kind == "video" and f.duration:
        inner += _field(3, DURATIONS[f.duration])
    out = b""
    if f.sort and kind == "video":
        out += _field(1, SORTS[f.sort])
    out += bytes([(2 << 3) | 2, len(inner)]) + inner
    return quote(base64.b64encode(out).decode(), safe="")
