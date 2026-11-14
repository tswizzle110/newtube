"""Seek-bar preview pictures: read from the video's own information, served once from this server, never leaking addresses."""
import httpx

from newtube.extract.base import Format, Storyboard, VideoInfo
from newtube.extract.ytdlp import parse_storyboard, parse_video_info
from newtube.services.videos import info_from_dict
from tests.test_api import FakeExtractor, make_client

import dataclasses


def sb_format(fid, width, frags=3, **kw):
    return {"format_id": fid, "format_note": "storyboard", "ext": "mhtml", "protocol": "mhtml", "vcodec": "none", "acodec": "none",
            "url": "https://i.ytimg.com/sb/x/storyboard3_L1/M0.jpg?sigh=a", "width": width, "height": width * 9 // 16, "columns": 5, "rows": 5,
            "fragments": [{"url": f"https://i.ytimg.com/sb/x/{fid}/M{i}.jpg?sigh=a", "duration": 25.0} for i in range(frags)], **kw}


def test_the_smallest_sharp_enough_set_is_chosen():
    raw = [sb_format("sb3", 320), sb_format("sb2", 160), sb_format("sb1", 80), sb_format("sb0", 48)]
    sb = parse_storyboard(raw)
    assert (sb.width, sb.height, sb.columns, sb.rows) == (160, 90, 5, 5) and len(sb.fragments) == 3
    assert parse_storyboard([sb_format("sb1", 80), sb_format("sb0", 48)]).width == 80                 # none sharp enough: the biggest
    assert parse_storyboard([]) is None and parse_storyboard([{"format_id": "18", "url": "https://x"}]) is None


def test_unusable_entries_are_ignored():
    broken = sb_format("sb2", 160); broken["fragments"] = [{"url": "http://insecure/x.jpg", "duration": 5}, {"url": "https://i.ytimg.com/a.jpg"}]
    assert parse_storyboard([broken]) is None                                                         # no usable fragment
    no_grid = sb_format("sb2", 160); no_grid.pop("columns")
    assert parse_storyboard([no_grid]) is None


def test_it_comes_with_the_video_info_and_survives_the_cache():
    info = parse_video_info({"id": "aaaaaaaaaaa", "title": "T", "formats": [sb_format("sb2", 160)]})
    assert info.storyboard and info.storyboard.width == 160
    again = info_from_dict(dataclasses.asdict(info))
    assert isinstance(again.storyboard, Storyboard) and again.storyboard.fragments[0][0].startswith("https://i.ytimg.com")
    assert info_from_dict(dataclasses.asdict(parse_video_info({"id": "aaaaaaaaaaa", "title": "T"}))).storyboard is None


class SbExtractor(FakeExtractor):
    async def video_info(self, video_id, *, background=False):
        self.calls += 1
        fm = [Format("18", "https://rr1.googlevideo.com/v", "mp4", "avc1.64001e", "mp4a.40.2", height=360, tbr=500)]
        return VideoInfo(video_id, "T", None, None, "", 60, 1, 1, None, [], None, False, 0, formats=fm, fetched_at=1,
                         storyboard=Storyboard(160, 90, 5, 5, [["https://i.ytimg.com/sb/x/M0.jpg?sigh=a", 25.0], ["https://i.ytimg.com/sb/x/M1.jpg?sigh=a", 25.0]]))


def test_the_page_gets_geometry_but_never_the_addresses(tmp_path):
    with make_client(tmp_path, lambda r: httpx.Response(200), SbExtractor()) as (client, ctx):
        j = client.get("/api/video/aaaaaaaaaaa").json()
        assert j["storyboard"] == {"w": 160, "h": 90, "cols": 5, "rows": 5, "seconds": [25.0, 25.0]}
        assert "ytimg" not in client.get("/api/video/aaaaaaaaaaa").text


def test_a_sheet_is_fetched_once_then_served_from_this_server(tmp_path):
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(200, content=b"JPEGDATA", headers={"content-type": "image/jpeg"})

    with make_client(tmp_path, handler, SbExtractor()) as (client, ctx):
        for _ in range(3):
            r = client.get("/api/sb/aaaaaaaaaaa/0")
            assert r.status_code == 200 and r.content == b"JPEGDATA" and r.headers["content-type"] == "image/jpeg"
        assert len(seen) == 1 and seen[0].startswith("https://i.ytimg.com/sb/")                     # one request to YouTube, three views
        assert client.get("/api/sb/aaaaaaaaaaa/1").status_code == 200 and len(seen) == 2


def test_bad_indexes_and_missing_previews_are_404(tmp_path):
    with make_client(tmp_path, lambda r: httpx.Response(200, content=b"x"), SbExtractor()) as (client, ctx):
        assert client.get("/api/sb/aaaaaaaaaaa/2").status_code == 404 and client.get("/api/sb/aaaaaaaaaaa/-1").status_code == 404
        assert client.get("/api/sb/bad/0").status_code == 400
    with make_client(tmp_path, lambda r: httpx.Response(200), FakeExtractor()) as (client, ctx):          # a video without previews
        assert client.get("/api/sb/bbbbbbbbbbb/0").status_code == 404


def test_a_stale_address_triggers_one_fresh_lookup(tmp_path):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(403) if len(calls) == 1 else httpx.Response(200, content=b"OK", headers={"content-type": "image/jpeg"})

    ex = SbExtractor()
    with make_client(tmp_path, handler, ex) as (client, ctx):
        assert client.get("/api/sb/aaaaaaaaaaa/0").status_code == 200 and ex.calls == 2
