import httpx

from newtube.config import Settings
from newtube.extract.base import ExtractionError, Format, VideoInfo
from newtube.net.http import OutboundClient
from newtube.net.ratelimit import RateLimiter
from newtube.selftest import SelfTest, format_results, redact_info, sponsorblock_prefix
from tests.fixtures_rss import SAMPLE_FEED


class FakeExtractor:
    def __init__(self, fail=False):
        self.fail = fail

    async def video_info(self, video_id, *, background=False):
        if self.fail:
            raise ExtractionError("Sign in to confirm you're not a bot")
        fmts = [Format("137", "https://r.googlevideo.com/v", "mp4", "avc1", None, height=1080),
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

    async def raw_playlist(self, playlist_id, limit=100, *, background=False):
        return {"entries": [{"id": "jNQXAC9IVRw", "title": "Zoo"}]}


def handler(req: httpx.Request) -> httpx.Response:
    if "videos.xml" in str(req.url):
        return httpx.Response(200, text=SAMPLE_FEED)
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
    for name in ("search_video", "search_channel", "search_playlist", "channel_videos", "playlist"):
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
