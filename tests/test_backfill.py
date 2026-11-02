"""The slow backfill of imported videos: order, budget, failures, and keeping imports out of the feed."""
import asyncio

import pytest

from newtube.config import Settings
from newtube.db.connection import connect, migrate
from newtube.db.queries import subscriptions as subs
from newtube.extract.base import BotCheck, ChannelInfo, ExtractionError, VideoInfo, VideoUnavailable
from newtube.importers import takeout as tk
from newtube.jobs import backfill as bf
from newtube.jobs.scheduler import Scheduler
from tests import fixtures_takeout as fx

NOW = 1_800_000_000
CH = "UC" + "a" * 22


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    return c


class FakeExtractor:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail or {}

    async def video_info(self, vid, *, background=False):
        assert background, "backfill must use the background lane"
        self.calls.append(vid)
        if vid in self.fail:
            raise self.fail[vid]
        return VideoInfo(vid, f"Real {vid}", CH, "Chan", "desc", 321, 1_600_000_000, 5, None, [], "http://t/x.jpg", False, 0,
                         fetched_at=NOW)


def add(conn, vid, *events, from_import=1):
    conn.execute("INSERT OR IGNORE INTO videos(id,title,first_seen_at,from_import) VALUES (?,?,?,?)", (vid, "", 1, from_import))
    for typ, ts in events:
        conn.execute("INSERT INTO events(ts,type,video_id) VALUES (?,?,?)", (ts, typ, vid))


def run(coro):
    return asyncio.run(coro)


def make(conn, extractor, cap=300):
    return bf.Backfiller(conn, extractor, cap, clock=lambda: NOW)


# ---- order ------------------------------------------------------------------------------------------

def test_most_useful_first_then_most_recent(conn):
    add(conn, "playlistonly", ("import_playlist_add", 900))
    add(conn, "watchedonce1", ("import_watch", 100))
    add(conn, "watchedonce2", ("import_watch", 200))                      # more recent than ...1
    add(conn, "rewatched001", ("import_watch", 50), ("import_watch", 60))
    add(conn, "watchlater01", ("import_watch_later", 10))
    add(conn, "liked0000001", ("import_like", 5), ("import_watch", 999))  # a like beats everything
    assert bf.next_ids(conn, NOW, limit=10) == ["liked0000001", "watchlater01", "rewatched001",
                                                "watchedonce2", "watchedonce1", "playlistonly"]


def test_only_imported_unfetched_videos_are_queued(conn):
    add(conn, "fetched00001", ("import_watch", 1)); conn.execute("UPDATE videos SET detail_fetched_at=5 WHERE id='fetched00001'")
    add(conn, "feedvideo001", ("import_watch", 1), from_import=0)         # seen in a real feed: not ours
    add(conn, "noevent00001")                                             # no import event: no reason to fetch
    add(conn, "wanted000001", ("import_watch", 1))
    assert bf.next_ids(conn, NOW, limit=10) == ["wanted000001"]


# ---- fetching ---------------------------------------------------------------------------------------

def test_a_fetch_fills_in_the_details_and_stops_it_being_queued(conn):
    add(conn, "wanted000001", ("import_watch", 1))
    ex = FakeExtractor()
    assert run(make(conn, ex).step()) == "fetched" and ex.calls == ["wanted000001"]
    v = conn.execute("SELECT * FROM videos WHERE id='wanted000001'").fetchone()
    assert (v["title"], v["duration"], v["channel_id"], v["detail_fetched_at"]) == ("Real wanted000001", 321, CH, NOW)
    assert bf.next_ids(conn, NOW) == [] and run(make(conn, ex).step()) == "idle"
    assert conn.execute("SELECT count(*) FROM events WHERE type='backfill'").fetchone()[0] == 1


def test_details_are_not_cached_as_playable_info(conn):
    add(conn, "wanted000001", ("import_watch", 1))
    run(make(conn, FakeExtractor()).step())
    assert conn.execute("SELECT count(*) FROM meta_cache").fetchone()[0] == 0     # stream URLs nobody will use


def test_a_finished_import_does_not_flood_the_subscriptions_feed(conn):
    subs.subscribe(conn, ChannelInfo(CH, "Chan"))
    add(conn, "wanted000001", ("import_watch", 1))
    run(make(conn, FakeExtractor()).step())
    assert conn.execute("SELECT published_at FROM videos WHERE id='wanted000001'").fetchone()[0] == 1_600_000_000
    assert subs.get_feed(conn) == []                                              # dated, but not "a new upload"


def test_a_video_later_seen_in_the_real_feed_joins_it(conn):
    subs.subscribe(conn, ChannelInfo(CH, "Chan"))
    add(conn, "wanted000001", ("import_watch", 1))
    from newtube.extract.rss import Feed, FeedEntry
    subs.store_feed(conn, Feed(CH, "Chan", [FeedEntry("wanted000001", "T", 1_700_000_000, "", 5, None)]))
    assert [r["id"] for r in subs.get_feed(conn)] == ["wanted000001"]


# ---- budget -----------------------------------------------------------------------------------------

def test_the_daily_budget_is_respected_and_counted_over_a_rolling_day(conn):
    for i in range(5):
        add(conn, f"video{i:06d}0", ("import_watch", i))
    ex = FakeExtractor()
    b = make(conn, ex, cap=2)
    assert [run(b.step()) for _ in range(3)] == ["fetched", "fetched", "capped"] and len(ex.calls) == 2
    later = bf.Backfiller(conn, ex, 2, clock=lambda: NOW + 86400 + 5)             # a day later: budget is back
    assert run(later.step()) == "fetched"


def test_the_budget_survives_a_restart(conn):
    for i in range(3):
        add(conn, f"video{i:06d}0", ("import_watch", i))
    run(make(conn, FakeExtractor(), cap=1).step())
    assert run(make(conn, FakeExtractor(), cap=1).step()) == "capped"             # a new object, same database


def test_a_cap_of_zero_turns_it_off(conn):
    add(conn, "wanted000001", ("import_watch", 1))
    ex = FakeExtractor()
    assert run(make(conn, ex, cap=0).step()) == "idle" and ex.calls == []


# ---- failures ---------------------------------------------------------------------------------------

def test_private_or_deleted_videos_are_never_asked_for_again(conn):
    add(conn, "gone0000001", ("import_watch", 1))
    ex = FakeExtractor({"gone0000001": VideoUnavailable("Video unavailable")})
    assert run(make(conn, ex).step()) == "gone"
    assert run(make(conn, ex).step()) == "idle" and ex.calls == ["gone0000001"]
    assert conn.execute("SELECT status FROM backfill_state").fetchone()[0] == "gone"


def test_other_failures_retry_later_with_growing_delays_then_give_up(conn):
    add(conn, "flaky000001", ("import_watch", 1))
    ex = FakeExtractor({"flaky000001": ExtractionError("HTTP 500")})
    t = NOW
    for expected, delay in (("retry", 2 * 3600), ("retry", 6 * 3600), ("retry", 24 * 3600), ("gone", None)):
        b = bf.Backfiller(conn, ex, 300, clock=lambda t=t: t)
        assert run(b.step()) == expected
        if delay:
            assert run(bf.Backfiller(conn, ex, 300, clock=lambda t=t: t + delay - 1).step()) == "idle"   # not yet
            t += delay
    assert len(ex.calls) == 4 and conn.execute("SELECT status, attempts FROM backfill_state").fetchone()[:] == ("gone", 4)


def test_an_unexpected_error_is_a_failed_attempt_not_a_crash(conn):
    add(conn, "weird0000001", ("import_watch", 1))
    assert run(make(conn, FakeExtractor({"weird0000001": KeyError("x")})).step()) == "retry"


def test_a_bot_check_pauses_everything_without_blaming_the_video(conn):
    add(conn, "wanted000001", ("import_watch", 1)); add(conn, "wanted000002", ("import_watch", 0))
    ex = FakeExtractor({"wanted000001": BotCheck("sign in to confirm")})
    b = make(conn, ex)
    assert run(b.step()) == "paused" and run(b.step()) == "paused" and len(ex.calls) == 1
    assert conn.execute("SELECT count(*) FROM backfill_state").fetchone()[0] == 0


# ---- end to end -------------------------------------------------------------------------------------

def test_after_a_real_import_it_works_through_the_queue_in_priority_order(conn, tmp_path):
    tk.run_import(conn, [str(fx.make_zip(tmp_path / "t.zip"))])
    ex = FakeExtractor()
    b = make(conn, ex)
    results = []
    while (r := run(b.step())) == "fetched":
        results.append(r)
    assert len(ex.calls) == 9 and r == "idle"                                     # every imported video, once
    liked = {row[0] for row in conn.execute("SELECT video_id FROM events WHERE type='import_like'")}
    assert set(ex.calls[:len(liked)]) == liked
    p = bf.progress(conn, 300, NOW)
    assert (p["waiting"], p["done"], p["gone"], p["used_last_day"]) == (0, 9, 0, 9)


def test_the_scheduler_backfills_only_when_no_channel_is_due(conn):
    add(conn, "wanted000001", ("import_watch", 1))
    s = Scheduler(Settings(), conn, http=None, extractor=FakeExtractor())
    assert run(s.tick()) == "idle"                                                # nothing due -> the loop moves on to backfill
    assert run(s.backfiller.step()) == "fetched"
