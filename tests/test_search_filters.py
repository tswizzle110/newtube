"""Search filters: the sp parameter is built exactly as YouTube's own menu builds it."""
import base64
from urllib.parse import unquote

import pytest

from newtube.extract import ytdlp
from newtube.extract.searchfilter import BadFilter, SearchFilter, build_sp

F = SearchFilter


def test_plain_kinds_match_the_constants_this_project_used_before():
    assert build_sp("video") == "EgIQAQ%3D%3D" and build_sp("channel") == "EgIQAg%3D%3D" and build_sp("playlist") == "EgIQAw%3D%3D"
    assert ytdlp.SEARCH_FILTERS == {"video": "EgIQAQ%3D%3D", "channel": "EgIQAg%3D%3D", "playlist": "EgIQAw%3D%3D"}


@pytest.mark.parametrize("flt,expected", [        # the values YouTube's own filter menu produces
    (F(duration="short"), "EgQQARgB"), (F(duration="long"), "EgQQARgC"), (F(duration="medium"), "EgQQARgD"),
    (F(upload_date="hour"), "EgQIARAB"), (F(upload_date="today"), "EgQIAhAB"), (F(upload_date="week"), "EgQIAxAB"),
    (F(upload_date="month"), "EgQIBBAB"), (F(upload_date="year"), "EgQIBRAB"),
])
def test_filters_match_youtubes_published_values(flt, expected):
    assert unquote(build_sp("video", flt)) == expected


@pytest.mark.parametrize("flt", [F(sort="date"), F(sort="views")])
def test_sorting_comes_first_then_the_video_type_filter(flt):
    raw = base64.b64decode(unquote(build_sp("video", flt)))
    assert raw[0] == 0x08 and raw[1] in (2, 3) and raw[2:] == bytes([0x12, 0x02, 0x10, 0x01])


def test_filters_combine():
    raw = base64.b64decode(unquote(build_sp("video", F("week", "long", "views"))))
    assert raw == bytes([0x08, 3, 0x12, 6, 0x08, 3, 0x10, 1, 0x18, 2])        # sort=views, {date=week, type=video, duration=long}


def test_date_duration_and_sort_do_not_leak_into_channel_or_playlist_searches():
    assert build_sp("channel", F("week", "long", "views")) == build_sp("channel")
    assert build_sp("playlist", F("today")) == build_sp("playlist")


def test_nothing_set_means_no_filter():
    assert not F() and F(sort="date") and F().key() == "-:-:-" and F("week", None, "date").key() == "week:-:date"


@pytest.mark.parametrize("bad", [F(upload_date="decade"), F(duration="forever"), F(sort="rating")])
def test_unknown_values_are_refused(bad):
    with pytest.raises(BadFilter):
        build_sp("video", bad)
    with pytest.raises(BadFilter):
        build_sp("movie")


def test_the_url_the_extractor_asks_for_carries_the_filters(monkeypatch):
    import asyncio
    from newtube.config import Settings
    from newtube.net.ratelimit import RateLimiter
    asked = []
    ex = ytdlp.YtDlpExtractor(Settings(), RateLimiter(0, 0, 10**6))

    async def fake_extract(url, extra, background):
        asked.append((url, extra))
        return {"entries": []}
    monkeypatch.setattr(ex, "_extract", fake_extract)
    asyncio.run(ex.search("piano jazz", "video", 10, filters=F("month", "long", "date")))
    asyncio.run(ex.search("piano jazz", "video", 10))
    assert asked[0][0].startswith("https://www.youtube.com/results?search_query=piano+jazz&sp=")
    assert unquote(asked[0][0].split("sp=")[1]) == unquote(build_sp("video", F("month", "long", "date")))
    assert asked[1][0].endswith("sp=EgIQAQ%3D%3D")
    with pytest.raises(Exception, match="bad search request"):
        asyncio.run(ex.search("x", "video", 10, filters=F(duration="forever")))


def test_search_endpoint_validates_and_forwards_filters(env):
    client, ctx, ex, seen = env
    got = []
    real = ex.search

    async def spy(query, kind="video", limit=20, *, background=False, **kw):
        got.append(kw)
        return await real(query, kind, limit, background=background)
    ex.search = spy
    ok = client.get("/api/search?q=zoo&kind=video&date=week&duration=long&sort=views")
    assert ok.status_code == 200 and got[-1]["filters"] == F("week", "long", "views")
    assert client.get("/api/search?q=zoo&kind=video&date=decade").status_code == 400
    client.get("/api/search?q=zoo2&kind=channel&date=week")
    assert "filters" not in got[-1]                                              # ignored for channels
    client.get("/api/search?q=zoo3&kind=video")
    assert "filters" not in got[-1]
    payloads = [r[0] for r in ctx.conn.execute("SELECT payload FROM events WHERE type='search' ORDER BY id")]
    import json
    assert json.loads(payloads[0])["filters"] == {"date": "week", "duration": "long", "sort": "views"}


from tests.test_api import env  # noqa: E402,F401
