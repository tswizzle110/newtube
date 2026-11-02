"""The long-lived yt-dlp session: reuse, option scoping, serialisation (with a fake YoutubeDL)."""
import asyncio
import threading
import time

import pytest
import yt_dlp

from newtube.config import Settings
from newtube.extract import ytdlp
from newtube.extract.base import BotCheck
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

    def extract_info(self, url, download=False):
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.03)
            self.seen.append(dict(self.params))
            if self.fail_next:
                exc, self.fail_next = self.fail_next, None
                raise exc
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
    FakeYDL.instances[0].fail_next = yt_dlp.utils.DownloadError("Sign in to confirm you’re not a bot")
    with pytest.raises(BotCheck):
        await ex._extract("u", {"getcomments": True}, False)
    assert (await ex._extract("u", {}, False)) == {"url": "u"}
    assert "getcomments" not in FakeYDL.instances[0].seen[-1]
