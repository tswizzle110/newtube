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


# ---- a failed lookup is remembered briefly, so retries cost nothing --------------------------------------

def test_the_same_failing_lookup_is_not_repeated_within_a_short_while():
    clock = Clock()
    ex = extractor([yt_dlp.utils.DownloadError("HTTP Error 500: Internal Server Error")] * 5, clock)
    from newtube.extract.base import ExtractionError
    for _ in range(6):                                           # the player, the page and Shaka all retrying
        with pytest.raises(ExtractionError):
            asyncio.run(ex.raw_video_info("aaaaaaaaaaa"))
    assert ex._sessions[False].calls == 1                        # YouTube was asked once
    with pytest.raises(ExtractionError):
        asyncio.run(ex.raw_video_info("bbbbbbbbbbb"))            # a DIFFERENT video is a different question
    assert ex._sessions[False].calls == 2


def test_the_memory_of_a_failure_expires():
    ex = extractor([yt_dlp.utils.DownloadError("HTTP Error 500"), {"id": "ok"}], Clock())
    from newtube.extract.base import ExtractionError
    with pytest.raises(ExtractionError):
        asyncio.run(ex.raw_video_info("aaaaaaaaaaa"))
    for key in list(ex._failures):
        ex._failures[key] = (0.0, ex._failures[key][1])          # (the 45 seconds have passed)
    assert asyncio.run(ex.raw_video_info("aaaaaaaaaaa")) == {"id": "ok"}


# ---- the gate survives a restart --------------------------------------------------------------------------

def test_a_bot_check_is_still_remembered_after_a_restart(tmp_path):
    from newtube.db.connection import connect, migrate
    conn = connect(tmp_path / "t.db"); migrate(conn)
    clock = Clock()
    first = BotGate(clock); first.persist_to(conn)
    first.trip(background=True)                                  # YouTube refuses; the server then restarts
    second = BotGate(clock); second.persist_to(conn)             # a brand-new process, same database
    with pytest.raises(BotCheck):
        second.check(background=True)
    with pytest.raises(BotCheck):
        second.check(background=False)
    assert second.trips == 1 and second.background_until == first.background_until
    second.ok()
    third = BotGate(clock); third.persist_to(conn)
    third.check(background=True); third.check(background=False)  # clearing it is remembered too


def test_a_damaged_gate_record_means_no_known_trouble(tmp_path):
    from newtube.db.connection import connect, migrate
    conn = connect(tmp_path / "t.db"); migrate(conn)
    conn.execute("INSERT INTO settings(key, value) VALUES ('bot_gate', 'not json')")
    g = BotGate(Clock()); g.persist_to(conn)
    g.check(background=True)


def test_the_normal_case_does_not_write_to_the_database_on_every_success(tmp_path):
    from newtube.db.connection import connect, migrate
    conn = connect(tmp_path / "t.db"); migrate(conn)
    g = BotGate(Clock()); g.persist_to(conn)
    before = conn.total_changes
    for _ in range(50):
        g.ok()
    assert conn.total_changes == before


# ---- EVERY way into YouTube obeys the gate (a guard against a future change quietly bypassing it) -----------

CHAN = "UC" + "a" * 22


def test_with_the_gate_closed_no_yt_dlp_route_reaches_youtube():
    from newtube.extract.ytdlp import _ScanSession
    clock = Clock()
    ex = extractor([], clock)

    class Boom:
        calls = 0

        def scan(self, *a, **k):
            Boom.calls += 1
            return {"entries": []}
    ex._scan_session = Boom()
    ex.gate.trip(background=True)                                # closed for background work AND for the next minute of clicks
    calls = [
        lambda bg: ex.video_info("aaaaaaaaaaa", background=bg),
        lambda bg: ex.raw_video_info("aaaaaaaaaaa", background=bg),
        lambda bg: ex.resolve_channel(CHAN, background=bg),
        lambda bg: ex.search("zoo", "video", 5, background=bg),
        lambda bg: ex.raw_search("zoo", "channel", 5, background=bg),
        lambda bg: ex.channel_videos(CHAN, 5, background=bg),
        lambda bg: ex.recent_uploads(CHAN, 5, background=bg),
        lambda bg: ex.channel_uploads(CHAN, 50, background=bg),
        lambda bg: ex.raw_uploads(CHAN, 50, background=bg),
        lambda bg: ex.playlist("PL" + "a" * 16, 5, background=bg),
        lambda bg: ex.playlist_page("PL" + "a" * 16, 5, background=bg),
        lambda bg: ex.channel_page(CHAN, 5, background=bg),
        lambda bg: ex.related("aaaaaaaaaaa", 5, background=bg),
        lambda bg: ex.comments("aaaaaaaaaaa", "top", 5, background=bg),
    ]
    for background in (True, False):
        for call in calls:
            with pytest.raises(BotCheck):
                asyncio.run(call(background))
    assert ex._sessions[False].calls == 0 and ex._sessions[True].calls == 0 and Boom.calls == 0


# ---- a channel scan that hits a bot check stays recognisable (so the page gets the plain 429, not a vague 502) -----

def test_a_failed_channel_scan_is_reported_as_a_bot_check(env):
    client, ctx, ex, seen = env
    import time as _t
    from newtube.services.lists import ListService
    CID = "UC" + "z" * 22

    async def refuse(*a, **k):
        raise BotCheck("Sign in to confirm you're not a bot")
    ex.channel_uploads = refuse
    ctx.lists = ListService(ctx.conn, ex)
    ctx.conn.execute("INSERT INTO channels(id,title,created_at) VALUES (?,?,1)", (CID, "Z"))
    first = client.get(f"/api/channel/{CID}?sort=popular&refresh=true")                     # starts the scan
    for _ in range(40):
        if first.status_code == 429:
            break
        _t.sleep(0.05)
        first = client.get(f"/api/channel/{CID}?sort=popular")
    assert first.status_code == 429 and "not a bot" in first.json()["detail"]
