import httpx

from newtube.config import Settings
from newtube.extract.base import ExtractionError, Format, VideoInfo
from newtube.net.http import OutboundClient
from newtube.net.ratelimit import RateLimiter
from newtube.selftest import SelfTest, format_results, redact_info, sponsorblock_prefix
from tests.fixtures_rss import SAMPLE_FEED
from tests.test_dash import FTYP, MOOV, SIDX, box
from tests.test_webm import WEBM

MP4 = FTYP + MOOV + SIDX + box(b"mdat", b"d" * 200)


class FakeExtractor:
    def __init__(self, fail=False):
        self.fail = fail

    async def video_info(self, video_id, *, background=False):
        if self.fail:
            raise ExtractionError("Sign in to confirm you're not a bot")
        if video_id == "aqz-KE-bpKQ":          # the 4K test video: one H.264 and one VP9 stream, real container bytes
            from newtube.extract.base import Format as F2
            fm = [F2("137", "https://r.googlevideo.com/hd/h264", "mp4", "avc1.640028", None, height=1080, tbr=4000, dynamic_range="SDR"),
                  F2("313", "https://r.googlevideo.com/hd/vp9", "webm", "vp09.00.51.08", None, height=2160, tbr=15000, dynamic_range="SDR")]
            return VideoInfo(video_id, "Big Buck Bunny", "UC1", "c", "", 600, 0, 1, None, [], None, False, 0, formats=fm)
        fmts = [Format("137", "https://r.googlevideo.com/v?expire=1&ip=203.0.113.9&ipbits=0", "mp4", "avc1", None, height=1080),
                Format("140", "https://r.googlevideo.com/a", "m4a", None, "mp4a", tbr=128)]
        return VideoInfo(video_id, "Zoo", "UC1", "c", "", 19, 0, 1, None, [], None, False, 0,
                         formats=fmts)

    async def resolve_channel(self, ref, *, background=False):
        raise ExtractionError("nope")

    # synthetic yt-dlp flat results, one per kind
    async def raw_search(self, query, kind="video", limit=20, *, background=False):
        e = {"video": {"id": "jNQXAC9IVRw", "title": "Zoo", "duration": 19},
             "channel": {"id": "UC" + "a" * 22, "title": "YouTube", "url": "https://www.youtube.com/@YouTube"},
             "playlist": {"id": "PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI", "title": "Crash", "playlist_count": 3}}[kind]
        return {"entries": [e]}

    async def raw_channel_videos(self, channel_id, limit=30, *, background=False):
        return {"entries": [{"id": "jNQXAC9IVRw", "title": "Zoo", "duration": 19}]}

    async def raw_uploads(self, channel_id, limit=500, progress=None, *, background=False):
        # newest first; the 5th is the most viewed. Entries carry view counts, like real playlist entries.
        entries = [{"id": f"vid{i:08d}", "title": f"Video {i}", "duration": 60, "view_count": 1000 if i == 5 else 10 + i}
                   for i in range(60)]
        for n in range(20, 61, 20):
            if progress:
                progress(n, 900)                      # the real scan reports as pages arrive
        if progress:
            progress(60, 900)
        return {"playlist_count": 900, "entries": entries}

    async def raw_playlist(self, playlist_id, limit=100, *, background=False):
        return {"entries": [{"id": "jNQXAC9IVRw", "title": "Zoo"}]}

    async def raw_comments(self, video_id, sort="top", limit=20, *, replies_for=0, replies_per_thread=10,
                           background=False):
        cs = [{"id": f"Ugx{i}", "parent": "root", "text": f"comment {i}", "author": "A", "like_count": i}
              for i in range(5)]
        if replies_for:
            cs += [{"id": "Ugx0.r", "parent": "Ugx0", "text": "reply", "author": "B"}]
        return {"comments": cs, "comment_count": 99}


def handler(req: httpx.Request) -> httpx.Response:
    if "videos.xml" in str(req.url):
        return httpx.Response(200, text=SAMPLE_FEED)
    if "googlevideo" in str(req.url) and "/hd/" in str(req.url):
        import re as _re
        a, b = map(int, _re.match(r"bytes=(\d+)-(\d+)", req.headers["range"]).groups())
        data = WEBM if req.url.path.endswith("vp9") else MP4
        return httpx.Response(206, content=data[a:b + 1])
    if "googlevideo" in str(req.url):
        return httpx.Response(206, content=b"x" * 1024, headers={"content-range": "bytes 0-1023/99"})
    if "sponsor.ajay.app" in str(req.url):
        return httpx.Response(404)
    return httpx.Response(200, content=b"img", headers={"content-type": "image/jpeg"})


def make(extractor):
    http = OutboundClient(Settings(), RateLimiter(0, 0, 10), transport=httpx.MockTransport(handler))
    return SelfTest(Settings(js_runtime="python3"), extractor, http)


async def test_all_good_paths_report_ok_except_failing_channel():
    results = {r.name: r for r in await make(FakeExtractor()).run()}
    assert results["rss"].ok and results["video_info"].ok
    assert results["stream_range"].ok and "206" in results["stream_range"].detail
    assert results["sponsorblock"].ok
    assert not results["channel"].ok          # one failure doesn't stop the others
    assert "2 entries" in results["rss"].detail
    assert results["video_warm"].ok and "2nd video" in results["video_warm"].detail
    assert results["exit_ip"].ok and "203.0.113.9" in results["exit_ip"].detail
    assert results["video_hd"].ok, results["video_hd"].detail
    assert "avc1 up to 1080p (sidx at" in results["video_hd"].detail and "vp9 up to 2160p (Cues at" in results["video_hd"].detail
    assert "direct, no proxy configured" in results["exit_ip"].detail
    for name in ("search_video", "search_channel", "search_playlist", "channel_videos", "playlist",
                 "comments_top", "comment_replies", "channel_sort"):
        assert results[name].ok, (name, results[name].detail)


async def test_failure_is_reported_and_downstream_check_skips():
    results = {r.name: r for r in await make(FakeExtractor(fail=True)).run()}
    assert not results["video_info"].ok and "bot" in results["video_info"].detail
    assert not results["stream_range"].ok and "skipped" in results["stream_range"].detail
    assert results["thumbnail"].ok            # independent checks still ran
    assert "FAILED" in format_results(list(results.values()))


def test_redact_and_prefix():
    assert redact_info({"u": ["https://a.b/c?sig=SECRET&ip=1.2.3.4"]}) == {"u": ["https://a.b/c?REDACTED"]}
    p = sponsorblock_prefix("jNQXAC9IVRw")
    assert len(p) == 4 and all(c in "0123456789abcdef" for c in p)


async def test_rss_check_catches_a_feed_that_parses_but_reports_the_wrong_channel():
    wrong = SAMPLE_FEED.replace("<yt:channelId>BR8-60-B28hp2BmDPdntcQ</yt:channelId>",
                                "<yt:channelId>Zzzzzzzzzzzzzzzzzzzzzz</yt:channelId>")
    http = OutboundClient(Settings(), RateLimiter(0, 0, 10),
                          transport=httpx.MockTransport(lambda r: httpx.Response(200, text=wrong)))
    st = SelfTest(Settings(js_runtime="python3"), FakeExtractor(), http)
    res = {r.name: r for r in await st.run()}
    assert not res["rss"].ok and "!=" in res["rss"].detail


async def test_search_check_reports_what_was_parsed_when_the_kind_is_wrong(tmp_path):
    class Mixed(FakeExtractor):
        async def raw_search(self, query, kind="video", limit=20, *, background=False):
            return {"entries": [{"id": "jNQXAC9IVRw", "title": "a video, though we asked for channels"}]}
    http = OutboundClient(Settings(), RateLimiter(0, 0, 10), transport=httpx.MockTransport(handler))
    st = SelfTest(Settings(js_runtime="python3"), Mixed(), http, dump_dir=tmp_path)
    res = {r.name: r for r in await st.run()}
    assert not res["search_channel"].ok and "VideoSummary" in res["search_channel"].detail
    assert (tmp_path / "search_channel.json").exists() and "search_channel" in (tmp_path / "samples.txt").read_text()


async def test_exit_ip_reports_the_proxy_without_leaking_its_password():
    http = OutboundClient(Settings(), RateLimiter(0, 0, 10), transport=httpx.MockTransport(handler))
    st = SelfTest(Settings(js_runtime="python3", outbound_proxy="http://user:s3cret@127.0.0.1:8888"),
                  FakeExtractor(), http)
    res = {r.name: r for r in await st.run()}
    assert "via proxy http://***@127.0.0.1:8888" in res["exit_ip"].detail
    assert "s3cret" not in res["exit_ip"].detail and "s3cret" not in format_results(list(res.values()))


async def test_exit_ip_says_so_when_the_url_has_no_ip():
    class NoIp(FakeExtractor):
        async def video_info(self, video_id, *, background=False):
            v = await super().video_info(video_id)
            for f in v.formats:
                f.url = f.url.split("?")[0]
            return v
    http = OutboundClient(Settings(), RateLimiter(0, 0, 10), transport=httpx.MockTransport(handler))
    res = {r.name: r for r in await SelfTest(Settings(js_runtime="python3"), NoIp(), http).run()}
    assert not res["exit_ip"].ok and "can't be determined" in res["exit_ip"].detail


async def test_channel_sort_check_passes_on_a_realistic_uploads_list_and_names_the_top_video():
    http = OutboundClient(Settings(), RateLimiter(0, 0, 10), transport=httpx.MockTransport(handler))
    res = {r.name: r for r in await SelfTest(Settings(js_runtime="python3"), FakeExtractor(), http).run()}
    assert res["channel_sort"].ok and "60 uploads" in res["channel_sort"].detail and "Video 5" in res["channel_sort"].detail


async def test_channel_sort_check_fails_loudly_when_entries_have_no_view_counts():
    class NoViews(FakeExtractor):
        async def raw_uploads(self, channel_id, limit=500, progress=None, *, background=False):
            if progress:
                progress(60, None)
            return {"entries": [{"id": f"vid{i:08d}", "title": f"V{i}"} for i in range(60)]}
    http = OutboundClient(Settings(), RateLimiter(0, 0, 10), transport=httpx.MockTransport(handler))
    res = {r.name: r for r in await SelfTest(Settings(js_runtime="python3"), NoViews(), http).run()}
    assert not res["channel_sort"].ok and "view count" in res["channel_sort"].detail


async def test_channel_sort_check_fails_when_progress_never_follows_the_pages():
    class Silent(FakeExtractor):
        async def raw_uploads(self, channel_id, limit=500, progress=None, *, background=False):
            return {"entries": [{"id": f"vid{i:08d}", "title": f"V{i}", "view_count": i + 1} for i in range(60)]}
    http = OutboundClient(Settings(), RateLimiter(0, 0, 10), transport=httpx.MockTransport(handler))
    res = {r.name: r for r in await SelfTest(Settings(js_runtime="python3"), Silent(), http).run()}
    assert not res["channel_sort"].ok and "progress" in res["channel_sort"].detail
