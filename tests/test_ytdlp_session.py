"""The long-lived yt-dlp session: reuse, option scoping, serialisation (with a fake YoutubeDL)."""
import asyncio
import threading
import time

import pytest
import yt_dlp

from newtube.config import Settings
from newtube.extract import ytdlp
from newtube.extract.base import ExtractionError
from newtube.net.ratelimit import RateLimiter


class FakeYDL:
    instances: list["FakeYDL"] = []

    def __init__(self, params):
        self.params = dict(params)
        self.seen = []                 # params as they were DURING each call
        self.active = self.max_active = 0
        self.fail_next = None
        self._lock = threading.Lock()
        FakeYDL.instances.append(self)

    entries_factory = None                 # set by a test: a generator of flat playlist entries

    def extract_info(self, url, download=False, process=True):
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.03)
            self.seen.append(dict(self.params))
            if self.fail_next:
                exc, self.fail_next = self.fail_next, None
                raise exc
            if not process:                # a playlist whose entries are produced page by page
                return {"playlist_count": 4321, "entries": FakeYDL.entries_factory()}
            return {"url": url}
        finally:
            with self._lock:
                self.active -= 1

    def sanitize_info(self, info):
        return info


@pytest.fixture
def ex(monkeypatch):
    FakeYDL.instances = []
    monkeypatch.setattr(ytdlp.yt_dlp, "YoutubeDL", FakeYDL)
    return ytdlp.YtDlpExtractor(Settings(), RateLimiter(0, 0, 100))


async def test_one_instance_is_reused_across_calls_per_lane(ex):
    for _ in range(3):
        await ex._extract("https://www.youtube.com/watch?v=aaaaaaaaaaa", {}, False)
    assert len(FakeYDL.instances) == 1                 # not one per call (that was the slow way)
    await ex._extract("https://www.youtube.com/watch?v=aaaaaaaaaaa", {}, True)
    assert len(FakeYDL.instances) == 2                 # the background lane has its own session


async def test_per_call_options_apply_only_for_that_call(ex):
    await ex._extract("u", {"playlistend": 5, "getcomments": True}, False)
    await ex._extract("u", {}, False)
    ydl = FakeYDL.instances[0]
    assert ydl.seen[0]["playlistend"] == 5 and ydl.seen[0]["getcomments"] is True
    assert "playlistend" not in ydl.seen[1] and "getcomments" not in ydl.seen[1]   # restored/removed
    assert ydl.seen[1]["noplaylist"] is True                                         # base option untouched


async def test_overridden_base_option_is_restored(ex):
    await ex._extract("u", {"noplaylist": False}, False)
    await ex._extract("u", {}, False)
    assert [s["noplaylist"] for s in FakeYDL.instances[0].seen] == [False, True]


async def test_concurrent_calls_on_one_lane_never_overlap(ex):
    await asyncio.gather(*(ex._extract(f"u{i}", {}, False) for i in range(5)))
    assert FakeYDL.instances[0].max_active == 1        # yt-dlp is not safe for concurrent use


async def test_background_work_does_not_block_interactive(ex):
    t0 = time.monotonic()
    await asyncio.gather(ex._extract("bg1", {}, True), ex._extract("bg2", {}, True), ex._extract("ui", {}, False))
    assert max(i.max_active for i in FakeYDL.instances) == 1
    assert len(FakeYDL.instances) == 2 and time.monotonic() - t0 < 0.15


async def test_a_failed_extraction_does_not_poison_the_session(ex):
    await ex._extract("u", {}, False)
    FakeYDL.instances[0].fail_next = yt_dlp.utils.DownloadError("HTTP Error 500: Internal Server Error")
    with pytest.raises(ExtractionError):
        await ex._extract("u", {"getcomments": True}, False)
    assert (await ex._extract("u", {}, False)) == {"url": "u"}
    assert "getcomments" not in FakeYDL.instances[0].seen[-1]


# ---- scanning a long playlist (the uploads list) -------------------------------------------------

UC = "UC" + "a" * 22


def entries(n, pulled=None, fail_at=None):
    def gen():
        for i in range(n):
            if pulled is not None:
                pulled.append(i)
            if fail_at == i:
                raise yt_dlp.utils.DownloadError("HTTP Error 500: Internal Server Error")
            yield {"id": f"vid{i:08d}", "title": f"V{i}", "view_count": i, "duration": 60}
    return gen


async def test_scan_pulls_pages_lazily_reports_progress_and_stops_at_the_limit(ex):
    pulled, seen = [], []
    FakeYDL.entries_factory = entries(100_000, pulled)
    videos, total = await ex.channel_uploads(UC, 250, lambda done, tot: seen.append((done, tot)))
    assert len(videos) == 250 and total == 4321
    assert len(pulled) <= 251                                   # it stopped asking for more pages
    assert seen[0] == (1, 4321) and seen[-1] == (250, 4321) and len(seen) == 250
    assert videos[0].view_count == 0 and videos[-1].id == "vid00000249" and videos[0].channel_id == UC


async def test_scan_uses_its_own_session_with_paced_requests_and_restores_options(ex):
    FakeYDL.entries_factory = entries(5)
    await ex.channel_uploads(UC, 10)
    assert len(FakeYDL.instances) == 1
    ydl = FakeYDL.instances[0]
    assert ydl.seen[0]["extract_flat"] == "in_playlist" and ydl.seen[0]["sleep_interval_requests"] == ex._settings.min_request_interval
    assert "sleep_interval_requests" not in ydl.params and "extract_flat" not in ydl.params
    await ex._extract("https://www.youtube.com/watch?v=aaaaaaaaaaa", {}, False)
    assert len(FakeYDL.instances) == 2                          # interactive work never shares the scan's session


async def test_a_scan_does_not_block_interactive_extractions(ex):
    FakeYDL.entries_factory = entries(40)                       # each pulled entry costs nothing; the call itself is slow
    t0 = time.monotonic()
    await asyncio.gather(ex.channel_uploads(UC, 40), ex._extract("u", {}, False), ex._extract("u2", {}, True))
    assert time.monotonic() - t0 < 0.2
    assert len(FakeYDL.instances) == 3


async def test_a_failure_halfway_is_classified_and_the_session_stays_usable(ex):
    ex.FAILURE_MEMORY = 0                      # (this test is about the session, not about remembering failures)
    FakeYDL.entries_factory = entries(10, fail_at=4)
    with pytest.raises(ExtractionError):
        await ex.channel_uploads(UC, 10)
    FakeYDL.entries_factory = entries(3)
    videos, _ = await ex.channel_uploads(UC, 10)
    assert len(videos) == 3 and "extract_flat" not in FakeYDL.instances[0].params


async def test_scan_rejects_bad_channel_ids_and_clamps_the_limit(ex):
    from newtube.extract.base import ExtractionError
    with pytest.raises(ExtractionError):
        await ex.channel_uploads("not-a-channel")
    FakeYDL.entries_factory = entries(20_000)
    videos, _ = await ex.channel_uploads(UC, 10**9)
    assert len(videos) == 10_000                                # MAX_UPLOADS
