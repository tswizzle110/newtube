"""After YouTube's "not a bot" answer: stop asking, say so plainly, and recover by itself."""
import asyncio

import pytest
import yt_dlp

from newtube.config import Settings
from newtube.extract.base import BotCheck
from newtube.extract.botgate import BACKGROUND_PAUSE, PROBE_EVERY, BotGate
from newtube.extract.ytdlp import YtDlpExtractor
from newtube.net.ratelimit import RateLimiter

BOT = "ERROR: [youtube] kgRdnn3Yrfw: Sign in to confirm you’re not a bot. Use --cookies-from-browser or --cookies for the authentication."


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


# ---- the gate on its own --------------------------------------------------------------------------------

def test_a_fresh_gate_lets_everything_through():
    g = BotGate(Clock())
    g.check(background=False); g.check(background=True)
    assert not g.closed


def test_a_bot_check_keeps_background_work_away_for_hours_and_lets_clicks_probe_once_a_minute():
    clock = Clock(); g = BotGate(clock)
    g.trip(background=False)
    with pytest.raises(BotCheck):
        g.check(background=True)                                 # background: away
    with pytest.raises(BotCheck):
        g.check(background=False)                                # a click right after: fail at once, no request
    clock.t += PROBE_EVERY + 1
    g.check(background=False)                                    # a minute later a click may probe
    clock.t += BACKGROUND_PAUSE[0] - PROBE_EVERY
    g.check(background=True)                                     # after the pause, background may try again


def test_a_click_probe_that_is_refused_again_does_not_lengthen_the_background_pause():
    clock = Clock(); g = BotGate(clock)
    g.trip(background=False)
    until = g.background_until
    for _ in range(5):
        clock.t += PROBE_EVERY + 1
        g.check(background=False)
        g.trip(background=False)                                 # the probe was refused too
    assert g.background_until == until and g.trips == 1


def test_background_refusals_escalate_6_then_12_then_24_hours_and_never_beyond():
    clock = Clock(); g = BotGate(clock)
    pauses = []
    for _ in range(5):
        g.trip(background=True)
        pauses.append(g.background_until - clock.t)
        clock.t = g.background_until + 1
    assert pauses == [float(p) for p in (*BACKGROUND_PAUSE, BACKGROUND_PAUSE[-1], BACKGROUND_PAUSE[-1])]


def test_any_success_reopens_the_gate_and_resets_the_escalation():
    clock = Clock(); g = BotGate(clock)
    g.trip(background=True); g.trip(background=True)
    g.ok()
    g.check(background=True); g.check(background=False)
    g.trip(background=True)
    assert g.background_until - clock.t == BACKGROUND_PAUSE[0]


def test_retry_after_is_a_sensible_number_of_seconds():
    clock = Clock(); g = BotGate(clock)
    g.trip(background=False)
    assert 1 <= g.retry_after() <= PROBE_EVERY + 1


# ---- in the extractor: it really does stop asking YouTube ------------------------------------------------

class FakeSession:
    def __init__(self, outcomes):
        self.outcomes, self.calls = list(outcomes), 0

    def run(self, url, extra):
        self.calls += 1
        out = self.outcomes.pop(0) if self.outcomes else {"entries": []}
        if isinstance(out, Exception):
            raise out
        return out


def extractor(outcomes, clock, background=()):
    ex = YtDlpExtractor(Settings(), RateLimiter(0, 0, 10**6))
    ex.gate = BotGate(clock)
    ex._sessions = {False: FakeSession(outcomes), True: FakeSession(background)}     # what you click / what runs in the background
    return ex


def test_after_a_bot_check_further_requests_fail_instantly_without_touching_youtube():
    clock = Clock()
    ex = extractor([yt_dlp.utils.DownloadError(BOT)], clock)
    with pytest.raises(BotCheck):
        asyncio.run(ex.raw_video_info("aaaaaaaaaaa"))                       # the real refusal
    for _ in range(10):
        with pytest.raises(BotCheck):
            asyncio.run(ex.raw_video_info("aaaaaaaaaaa"))                   # retries, page reloads, other callers...
        with pytest.raises(BotCheck):
            asyncio.run(ex.raw_video_info("bbbbbbbbbbb", background=True))
    assert ex._sessions[False].calls == 1 and ex._sessions[True].calls == 0   # YouTube was asked exactly once


def test_a_click_probes_after_a_minute_and_a_success_clears_everything():
    clock = Clock()
    ex = extractor([yt_dlp.utils.DownloadError(BOT), {"id": "aaaaaaaaaaa"}], clock)
    with pytest.raises(BotCheck):
        asyncio.run(ex.raw_video_info("aaaaaaaaaaa"))
    clock.t += PROBE_EVERY + 1
    assert asyncio.run(ex.raw_video_info("aaaaaaaaaaa")) == {"id": "aaaaaaaaaaa"}   # YouTube relented
    assert not ex.gate.closed
    assert asyncio.run(ex.raw_video_info("ccccccccccc", background=True)) is not None   # background is welcome back at once


def test_other_errors_do_not_close_the_gate():
    clock = Clock()
    ex = extractor([yt_dlp.utils.DownloadError("ERROR: Video unavailable"), {"id": "x"}], clock)
    with pytest.raises(Exception):
        asyncio.run(ex.raw_video_info("aaaaaaaaaaa"))
    assert not ex.gate.closed and asyncio.run(ex.raw_video_info("bbbbbbbbbbb")) == {"id": "x"}


# ---- what the browser is told ---------------------------------------------------------------------------

def test_every_endpoint_answers_a_bot_check_with_the_same_plain_429(env):
    client, ctx, ex, seen = env

    async def refuse(*a, **k):
        raise BotCheck("Sign in to confirm you're not a bot")
    ex.video_info = refuse
    ex.search = refuse
    ctx.videos.extractor = ex
    for url in ("/api/video/aaaaaaaaaaa", "/api/dash/aaaaaaaaaaa.mpd", "/api/stream/aaaaaaaaaaa/18", "/api/search?q=zoo&kind=video"):
        r = client.get(url)
        assert r.status_code == 429, (url, r.status_code, r.text[:100])
        assert "not a bot" in r.json()["detail"] and "cookies" not in r.json()["detail"].lower()
        assert int(r.headers["retry-after"]) >= 1


from tests.test_api import env  # noqa: E402,F401
