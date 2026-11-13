"""Channel pictures for subscriptions that have none: found slowly in the background, never twice in a row, never past
the daily allowance, and a stale or broken one is replaced instead of leaving an empty circle."""
import asyncio
import time

import httpx
import pytest

from newtube.config import Settings
from newtube.db.connection import connect, migrate
from newtube.db.queries import subscriptions as subs
from newtube.extract.base import BotCheck, ChannelInfo, ExtractionError
from newtube.jobs.scheduler import Scheduler
from newtube.net.ratelimit import DailyCapReached
from tests.test_api import env  # noqa: F401  (a fixture)

A, B, C = "UC" + "a" * 22, "UC" + "b" * 22, "UC" + "c" * 22


class Ex:
    def __init__(self, avatar="//yt3.ggpht.com/pic=s176", fail=None):
        self.avatar, self.fail, self.calls = avatar, fail, []

    async def resolve_channel(self, ref, *, background=False):
        self.calls.append((ref, background))
        if self.fail:
            raise self.fail
        return ChannelInfo(ref, "Title " + ref[2:4], None, self.avatar)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    return c


def sched(conn, ex, cap=40):
    return Scheduler(Settings(channel_picture_daily_cap=cap), conn, http=None, extractor=ex)


def run(c):
    return asyncio.run(c)


def has_picture(conn, cid):
    return conn.execute("SELECT avatar_url FROM channels WHERE id=?", (cid,)).fetchone()[0] is not None


def test_a_subscription_without_a_picture_gets_one_in_the_background(conn):
    subs.subscribe(conn, ChannelInfo(A, "Alpha"))                       # (subscribed with no picture, as an import does)
    ex = Ex()
    assert run(sched(conn, ex).picture_step()) == "pictured"
    assert has_picture(conn, A) and ex.calls == [(A, True)]            # one lookup, on the background lane
    assert run(sched(conn, ex).picture_step()) == "idle" and len(ex.calls) == 1


def test_channels_that_already_have_a_picture_are_left_alone(conn):
    subs.subscribe(conn, ChannelInfo(A, "Alpha", None, "//yt3.ggpht.com/x"))
    ex = Ex()
    assert run(sched(conn, ex).picture_step()) == "idle" and ex.calls == []


def test_one_per_step_newest_subscription_first(conn):
    subs.subscribe(conn, ChannelInfo(A, "Alpha")); conn.execute("UPDATE subscriptions SET subscribed_at=100 WHERE channel_id=?", (A,))
    subs.subscribe(conn, ChannelInfo(B, "Beta")); conn.execute("UPDATE subscriptions SET subscribed_at=200 WHERE channel_id=?", (B,))
    ex = Ex()
    run(sched(conn, ex).picture_step())
    assert ex.calls == [(B, True)] and has_picture(conn, B) and not has_picture(conn, A)


def test_the_daily_allowance_is_counted_in_the_database_so_a_restart_does_not_reset_it(conn):
    for cid in (A, B, C):
        subs.subscribe(conn, ChannelInfo(cid, "x"))
    ex = Ex()
    assert [run(sched(conn, ex, cap=2).picture_step()) for _ in range(3)] == ["pictured", "pictured", "capped"]
    assert run(sched(conn, ex, cap=2).picture_step()) == "capped"       # a brand-new scheduler (a restart): still used up
    assert len(ex.calls) == 2


def test_a_cap_of_zero_turns_it_off(conn):
    subs.subscribe(conn, ChannelInfo(A, "Alpha"))
    ex = Ex()
    assert run(sched(conn, ex, cap=0).picture_step()) == "idle" and ex.calls == []
    assert run(Scheduler(Settings(), conn, http=None, extractor=None).picture_step()) == "idle"


def test_a_channel_with_no_picture_to_find_is_not_asked_about_again_for_three_days(conn):
    subs.subscribe(conn, ChannelInfo(A, "Alpha"))
    ex = Ex(avatar=None)                                               # YouTube has no picture for it
    assert run(sched(conn, ex).picture_step()) == "pictured" and len(ex.calls) == 1
    assert run(sched(conn, ex).picture_step()) == "idle" and len(ex.calls) == 1          # not again straight away
    conn.execute("UPDATE events SET ts = ts - ? WHERE type='avatar_lookup'", (3 * 86400 + 5,))
    assert run(sched(conn, ex).picture_step()) == "pictured" and len(ex.calls) == 2      # but after three days, once more


def test_failures_are_counted_and_not_retried_at_once(conn):
    subs.subscribe(conn, ChannelInfo(A, "Alpha"))
    ex = Ex(fail=ExtractionError("HTTP 500"))
    assert run(sched(conn, ex).picture_step()) == "retry"
    assert run(sched(conn, ex).picture_step()) == "idle" and len(ex.calls) == 1


def test_a_bot_check_or_a_full_background_budget_stops_it_without_a_failure_note(conn):
    subs.subscribe(conn, ChannelInfo(A, "Alpha"))
    assert run(sched(conn, Ex(fail=BotCheck("sign in"))).picture_step()) == "paused"
    conn.execute("DELETE FROM events WHERE type='avatar_lookup'")                       # (so it is asked again)
    assert run(sched(conn, Ex(fail=DailyCapReached("cap"))).picture_step()) == "capped"


def test_the_pace_is_spread_over_the_day(conn):
    assert sched(conn, Ex(), cap=40).pause_after("pictured") == 86400 / 40
    assert sched(conn, Ex(), cap=0).pause_after("pictured") == 300.0


def test_step_runs_it_when_nothing_else_is_waiting(conn):
    subs.subscribe(conn, ChannelInfo(A, "Alpha", None, "//yt3.ggpht.com/x"))
    subs.subscribe(conn, ChannelInfo(B, "Beta"))
    ex = Ex()
    s = Scheduler(Settings(channel_picture_daily_cap=40, backfill_daily_cap=0, related_daily_cap=0), conn, http=None, extractor=ex)
    conn.execute("UPDATE channels SET rss_checked_at=? WHERE 1", (int(time.time()),))      # (feeds were just checked)
    assert run(s.step()) == "pictured" and has_picture(conn, B)


# ---- the picture itself ------------------------------------------------------------------------------

def test_the_subscription_list_says_who_has_a_picture(env):
    client, ctx, ex, seen = env
    subs.subscribe(ctx.conn, ChannelInfo(A, "Alpha", None, "//yt3.ggpht.com/x"))
    subs.subscribe(ctx.conn, ChannelInfo(B, "Beta"))
    got = {c["id"]: c["has_avatar"] for c in client.get("/api/subscriptions").json()}
    assert got == {A: True, B: False}


@pytest.mark.parametrize("status,forgotten", [(404, True), (403, True), (410, True), (500, False)])
def test_a_stale_picture_address_is_forgotten_so_it_is_found_again(tmp_path, status, forgotten):
    from tests.test_api import FakeExtractor, make_client
    handler = lambda req: httpx.Response(status)                                    # noqa: E731
    with make_client(tmp_path, handler, FakeExtractor()) as (client, ctx):
        subs.subscribe(ctx.conn, ChannelInfo(A, "Alpha", None, "https://yt3.ggpht.com/old=s176"))
        assert client.get(f"/api/avatar/{A}").status_code == 404
        assert has_picture(ctx.conn, A) is (not forgotten)
