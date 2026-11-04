"""Server-side stability: the background loop survives errors, and a connection that died is retried once."""
import asyncio

import httpx
import pytest

from newtube.config import Settings
from newtube.db.connection import connect, migrate
from newtube.jobs.scheduler import Scheduler
from newtube.net.http import OutboundClient
from newtube.net.ratelimit import RateLimiter


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    return c


# ---- the background loop never dies -------------------------------------------------------------------

def test_the_background_loop_keeps_going_after_a_step_blows_up(conn):
    sleeps, calls = [], []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 3:
            raise asyncio.CancelledError            # end the test: the loop would otherwise run forever

    s = Scheduler(Settings(), conn, http=None, extractor=None, sleep=fake_sleep, rand=lambda: 0.5)

    async def flaky_step():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("database is locked")           # an unexpected failure in the first round
        return "idle"
    s.step = flaky_step
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(s.run())
    assert len(calls) == 3                                   # it went on to rounds 2 and 3
    assert sleeps[0] == 60.0 and sleeps[1] == 300.0          # a minute after the failure, then the usual idle pause


def test_cancelling_the_loop_still_stops_it(conn):
    async def sleeper(seconds):
        raise asyncio.CancelledError
    s = Scheduler(Settings(), conn, http=None, extractor=None, sleep=sleeper)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(s.run())


def test_a_real_step_with_nothing_to_do_is_idle(conn):
    s = Scheduler(Settings(), conn, http=None, extractor=None)
    assert asyncio.run(s.step()) == "idle"


# ---- outbound requests ----------------------------------------------------------------------------------

def client(handler):
    return OutboundClient(Settings(), RateLimiter(0, 0, 10**6), transport=httpx.MockTransport(handler))


def test_a_dead_connection_is_retried_once_on_a_fresh_one():
    seen = []

    def handler(req):
        seen.append(1)
        if len(seen) == 1:
            raise httpx.ConnectError("connection reset")
        return httpx.Response(200, text="ok")
    r = asyncio.run(client(handler).get("https://www.youtube.com/x"))
    assert r.text == "ok" and len(seen) == 2


def test_it_gives_up_after_the_second_failure_and_media_requests_are_covered_too():
    def handler(req):
        raise httpx.ReadError("boom")
    with pytest.raises(httpx.ReadError):
        asyncio.run(client(handler).get("https://www.youtube.com/x"))
    with pytest.raises(httpx.ReadError):
        asyncio.run(client(handler).get_media("https://i.ytimg.com/vi/aaaaaaaaaaa/hqdefault.jpg"))


def test_http_error_statuses_are_answers_and_are_not_retried():
    seen = []

    def handler(req):
        seen.append(1)
        return httpx.Response(503)
    assert asyncio.run(client(handler).get("https://www.youtube.com/x")).status_code == 503 and len(seen) == 1


def test_a_post_is_never_repeated_automatically():
    seen = []

    def handler(req):
        seen.append(1)
        raise httpx.ConnectError("reset")
    with pytest.raises(httpx.ConnectError):
        asyncio.run(client(handler).post("https://sponsor.ajay.app/api/x", limited=False))
    assert len(seen) == 1


def test_the_limiter_is_charged_once_per_call_not_per_retry():
    calls = []

    class Counting(RateLimiter):
        async def acquire(self, lane=None):
            calls.append(1)
    seen = []

    def handler(req):
        seen.append(1)
        if len(seen) == 1:
            raise httpx.ConnectError("reset")
        return httpx.Response(200, text="ok")
    c = OutboundClient(Settings(), Counting(0, 0, 10**6), transport=httpx.MockTransport(handler))
    asyncio.run(c.get("https://www.youtube.com/x"))
    assert len(seen) == 2 and len(calls) == 1
