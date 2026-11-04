"""When RSS is unavailable: discover new uploads through the channel page, without ever
getting in the way of what you click."""
import asyncio
import time

import httpx
import pytest

from newtube.config import Settings
from newtube.db.connection import connect, migrate
from newtube.db.queries import subscriptions as subs
from newtube.extract.base import ChannelInfo, ExtractionError, VideoSummary
from newtube.jobs.scheduler import RETRY_AFTER, RSS_FAILURES_BEFORE_SKIP, RSS_SKIP_SECONDS, Scheduler
from newtube.net.http import OutboundClient
from newtube.net.ratelimit import Lane, RateLimiter
from tests.fixtures_rss import SAMPLE_FEED

CH = "UC" + "a" * 22


def vids(*ids):
    return [VideoSummary(i, f"title {i}", None, None, 100 + n) for n, i in enumerate(ids)]


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    return c


# ---- storing what the channel page reports -----------------------------------------------------

def test_new_videos_of_a_known_channel_join_the_feed_newest_first(conn):
    subs.subscribe(conn, ChannelInfo(CH, "Chan"))
    conn.execute("INSERT INTO videos(id,channel_id,title,published_at,first_seen_at) VALUES ('old00000001',?,'old',100,1)", (CH,))
    conn.execute("UPDATE channels SET feed_seeded=1 WHERE id=?", (CH,))          # its uploads were read once before
    n = subs.store_discovered(conn, CH, vids("new00000001", "new00000002", "old00000001"), now=10_000)
    assert n == 2
    feed = [r["id"] for r in subs.get_feed(conn)]
    assert feed == ["new00000001", "new00000002", "old00000001"]        # page order preserved, then older
    assert conn.execute("SELECT published_at FROM videos WHERE id='new00000001'").fetchone()[0] == 10_000


def test_first_poll_of_an_unknown_channel_remembers_ids_but_keeps_the_backlog_out_of_the_feed(conn):
    subs.subscribe(conn, ChannelInfo(CH, "Chan"))
    assert subs.store_discovered(conn, CH, vids("back0000001", "back0000002"), now=10_000) == 0
    assert subs.get_feed(conn) == []                                      # not presented as "new"
    assert conn.execute("SELECT count(*) FROM videos").fetchone()[0] == 2  # but remembered
    # next poll: one genuinely new upload at the top of the page -> only that one is new
    assert subs.store_discovered(conn, CH, vids("fresh000001", "back0000001", "back0000002"), now=20_000) == 1
    assert [r["id"] for r in subs.get_feed(conn)] == ["fresh000001"]


def test_known_videos_are_not_clobbered(conn):
    subs.subscribe(conn, ChannelInfo(CH, "Chan"))
    conn.execute("INSERT INTO videos(id,channel_id,title,published_at,view_count,first_seen_at) VALUES ('same0000001',?,'RSS title',555,9,1)", (CH,))
    subs.store_discovered(conn, CH, [VideoSummary("same0000001", "other", None, None, 77)], now=10_000)
    r = conn.execute("SELECT title, published_at, view_count, duration FROM videos WHERE id='same0000001'").fetchone()
    assert (r["title"], r["published_at"], r["view_count"], r["duration"]) == ("RSS title", 555, 9, 77)


# ---- the scheduler -----------------------------------------------------------------------------

class FakeExtractor:
    def __init__(self, result=None, fail=False):
        self.calls, self.result, self.fail = [], result or [], fail

    async def channel_videos(self, channel_id, limit=30, *, background=False):
        self.calls.append((channel_id, limit, background))
        if self.fail:
            raise ExtractionError("boom")
        return self.result


def make(conn, rss_status, extractor, clock=None, n_channels=1):
    requests = []

    def handler(req):
        requests.append(str(req.url))
        return httpx.Response(rss_status, text=SAMPLE_FEED) if rss_status == 200 else httpx.Response(rss_status)

    http = OutboundClient(Settings(), RateLimiter(0, 0, 1000), transport=httpx.MockTransport(handler))
    ids = [CH[:-1] + chr(ord("a") + i) for i in range(n_channels)]
    for cid in ids:
        subs.subscribe(conn, ChannelInfo(cid, f"Chan {cid[-1]}"))
    kw = {"clock": clock} if clock else {}
    return Scheduler(Settings(), conn, http, extractor=extractor, **kw), requests, ids


async def test_rss_404_falls_back_to_the_channel_page_in_the_background_lane(conn):
    conn.execute("INSERT INTO channels(id,title,created_at) VALUES (?,?,1)", (CH, "Chan"))
    ex = FakeExtractor(vids("new00000001"))
    sch, requests, [cid] = make(conn, 404, ex)
    conn.execute("INSERT INTO videos(id,channel_id,title,published_at,first_seen_at) VALUES ('old00000001',?,'o',1,1)", (cid,))
    conn.execute("UPDATE channels SET feed_seeded=1 WHERE id=?", (cid,))
    assert await sch.tick() == "polled"
    assert ex.calls == [(cid, 15, True)]                                  # background=True
    assert [r["id"] for r in subs.get_feed(conn)][0] == "new00000001"
    due = subs.due_for_poll(conn, Settings().rss_poll_interval, now=int(time.time()) + 60)
    assert due == []                                                      # healthy again: next poll in hours


async def test_rss_is_skipped_after_repeated_failures_then_retried_later(conn):
    t = [1000.0]
    ex = FakeExtractor()
    sch, requests, ids = make(conn, 404, ex, clock=lambda: t[0], n_channels=6)
    for _ in range(RSS_FAILURES_BEFORE_SKIP):
        await sch.tick()
    assert len(requests) == RSS_FAILURES_BEFORE_SKIP
    await sch.tick(); await sch.tick()
    assert len(requests) == RSS_FAILURES_BEFORE_SKIP                      # no more pointless 404s...
    assert len(ex.calls) == RSS_FAILURES_BEFORE_SKIP + 2                  # ...but channels are still covered
    t[0] += RSS_SKIP_SECONDS + 1
    await sch.tick()
    assert len(requests) == RSS_FAILURES_BEFORE_SKIP + 1                  # RSS gets another chance


async def test_one_rss_success_resets_the_failure_count(conn):
    ex = FakeExtractor()
    sch, requests, ids = make(conn, 404, ex, n_channels=4)
    await sch.tick(); await sch.tick()
    assert sch._rss_failures == 2
    # flip the mock network to healthy:
    sch.http = OutboundClient(Settings(), RateLimiter(0, 0, 1000),
                              transport=httpx.MockTransport(lambda r: httpx.Response(200, text=SAMPLE_FEED)))
    # the sample feed belongs to another channel id; poll_channel stores under the requested one
    await sch.tick()
    assert sch._rss_failures == 0


async def test_if_the_fallback_fails_too_the_channel_is_retried_in_ten_minutes(conn):
    ex = FakeExtractor(fail=True)
    sch, requests, [cid] = make(conn, 404, ex)
    assert await sch.tick() == "polled"
    checked = conn.execute("SELECT rss_checked_at FROM channels WHERE id=?", (cid,)).fetchone()[0]
    due_in = checked + Settings().rss_poll_interval - time.time()
    assert RETRY_AFTER - 10 <= due_in <= RETRY_AFTER + 10


async def test_without_an_extractor_behaviour_is_unchanged(conn):
    sch, requests, [cid] = make(conn, 404, None)
    assert await sch.tick() == "polled" and len(requests) == 1


# ---- "does it slow down what I click?" ----------------------------------------------------------

async def test_clicks_never_queue_behind_background_requests():
    rl = RateLimiter(0.1, 0, 10_000)

    async def background():
        for _ in range(50):
            await rl.acquire(Lane.BACKGROUND)

    tasks = [asyncio.create_task(background()) for _ in range(6)]     # a pile of waiting background work
    await asyncio.sleep(0.3)
    t0 = time.monotonic()
    await rl.acquire(Lane.INTERACTIVE)
    waited = time.monotonic() - t0
    for t in tasks:
        t.cancel()
    # At most the spacing of one already-sent request (0.1 s), NOT six queued slots (~0.6 s).
    assert waited < 0.22, f"interactive request waited {waited:.2f}s behind background work"


async def test_background_still_makes_progress_when_nothing_interactive_is_waiting():
    rl = RateLimiter(0.02, 0, 10_000)
    t0 = time.monotonic()
    for _ in range(5):
        await rl.acquire(Lane.BACKGROUND)
    assert 0.07 <= time.monotonic() - t0 < 0.4
    assert rl.used_today() == 5


async def test_housekeeping_removes_expired_cache_rows_but_at_most_once_an_hour(conn):
    """Scanned uploads lists can be megabytes; expired rows must not pile up in the database."""
    from newtube.db.queries import cache
    clock = [10_000.0]
    sch, _, _ = make(conn, 404, None, clock=lambda: clock[0])
    cache.put(conn, "old", {"x": 1}, ttl=10, now=100)               # expired long ago
    cache.put(conn, "fresh", {"x": 1}, ttl=10**9)
    assert sch.housekeeping() == 1
    assert {r["key"] for r in conn.execute("SELECT key FROM meta_cache")} == {"fresh"}
    cache.put(conn, "old2", {"x": 1}, ttl=10, now=100)
    clock[0] += 600
    assert sch.housekeeping() == 0 and conn.execute("SELECT count(*) FROM meta_cache").fetchone()[0] == 2   # too soon
    clock[0] += 3600
    assert sch.housekeeping() == 1


def test_imported_history_does_not_make_the_whole_backlog_look_new(conn):
    """Regression for the Takeout import: watch history creates (undated) video rows for a channel. That must
    not count as 'we know this channel's uploads', or its newest 15 uploads would flood the feed as new."""
    subs.subscribe(conn, ChannelInfo(CH, "Chan"))
    for i in range(3):          # what an import leaves behind: rows without a date, channel never polled
        conn.execute("INSERT INTO videos(id,channel_id,title,first_seen_at) VALUES (?,?,'',1)", (f"hist000000{i}", CH))
    assert subs.store_discovered(conn, CH, vids(*[f"page{i:07d}" for i in range(15)]), now=10_000) == 0
    assert subs.get_feed(conn) == []                                 # nothing presented as new
    assert conn.execute("SELECT feed_seeded FROM channels WHERE id=?", (CH,)).fetchone()[0] == 1
    assert subs.store_discovered(conn, CH, vids("fresh000001", *[f"page{i:07d}" for i in range(15)]), now=20_000) == 1
    assert [r["id"] for r in subs.get_feed(conn)] == ["fresh000001"]   # ...but a genuinely new one is


def test_rss_can_date_a_video_that_history_import_left_undated(conn):
    from newtube.extract.rss import Feed, FeedEntry
    subs.subscribe(conn, ChannelInfo(CH, "Chan"))
    conn.execute("INSERT INTO videos(id,channel_id,title,first_seen_at) VALUES ('watched0001',?,'',1)", (CH,))
    feed = Feed(CH, "Chan", [FeedEntry("watched0001", "A title", 1_700_000_000, None, None, 5)])
    subs.store_feed(conn, feed)
    r = conn.execute("SELECT title, published_at FROM videos WHERE id='watched0001'").fetchone()
    assert (r["title"], r["published_at"]) == ("A title", 1_700_000_000)
    assert conn.execute("SELECT feed_seeded FROM channels WHERE id=?", (CH,)).fetchone()[0] == 1


# ---- never invent "uploaded just now" ---------------------------------------------------------------

def test_a_page_full_of_unseen_videos_is_a_backlog_not_a_burst_of_new_uploads(conn):
    subs.subscribe(conn, ChannelInfo(CH, "Chan"))
    conn.execute("UPDATE channels SET feed_seeded=1 WHERE id=?", (CH,))
    page = vids(*[f"vid{i:08d}" for i in range(15)])
    assert subs.store_discovered(conn, CH, page, now=10_000) == 0
    assert subs.get_feed(conn) == []                                      # none of them is dated "now"
    assert conn.execute("SELECT count(*) FROM videos WHERE published_at IS NOT NULL").fetchone()[0] == 0


def test_a_few_unseen_videos_are_still_real_new_uploads(conn):
    subs.subscribe(conn, ChannelInfo(CH, "Chan"))
    conn.execute("UPDATE channels SET feed_seeded=1 WHERE id=?", (CH,))
    assert subs.store_discovered(conn, CH, vids("new00000001", "new00000002"), now=10_000) == 2


def test_migration_005_removes_invented_dates_and_keeps_real_ones(tmp_path):
    from newtube.db import connection
    c = connect(tmp_path / "old.db")
    files = sorted(connection.MIGRATIONS_DIR.glob("*.sql"))
    for m in files:
        if m.name.startswith("005"):
            break
        c.executescript(m.read_text())
    c.execute("INSERT INTO channels(id,title,created_at,rss_checked_at,feed_seeded) VALUES (?, 'C', 1, 500, 1)", (CH,))
    c.execute("INSERT INTO channels(id,title,created_at,feed_seeded) VALUES ('UCnever', 'N', 1, 1)")
    rows = [("invented0001", 10_000 - 3, 10_000, None),     # dated 3 s before it was first seen: invented
            ("realrss00001", 5_000, 10_000, None),          # a real date from a feed
            ("opened000001", 10_000 - 2, 10_000, 9_999)]    # opened: its date is real whatever it looks like
    for vid, pub, seen, detail in rows:
        c.execute("INSERT INTO videos(id,channel_id,title,published_at,first_seen_at,detail_fetched_at) VALUES (?,?,?,?,?,?)",
                  (vid, CH, vid, pub, seen, detail))
    c.executescript(next(m for m in files if m.name.startswith("005")).read_text())
    pub = dict(c.execute("SELECT id, published_at FROM videos").fetchall())
    assert pub == {"invented0001": None, "realrss00001": 5_000, "opened000001": 9_998}
    assert c.execute("SELECT rss_checked_at FROM channels WHERE id=?", (CH,)).fetchone()[0] is None   # poll it again soon
    assert c.execute("SELECT feed_seeded FROM channels WHERE id='UCnever'").fetchone()[0] == 0        # never really read


# ---- asking YouTube slowly: the channel-page lookup is limited and rare ---------------------------------------

async def test_an_rss_outage_cannot_become_a_burst_of_channel_lookups(conn):
    from newtube.jobs.scheduler import FALLBACK_PER_HOUR
    ex = FakeExtractor()
    sch, requests, ids = make(conn, 500, ex, n_channels=FALLBACK_PER_HOUR + 5)
    for _ in range(FALLBACK_PER_HOUR + 5):
        await sch.tick()
    assert len(ex.calls) == FALLBACK_PER_HOUR                              # the rest were left alone (and queued for later)
    waiting = [r[0] for r in conn.execute("SELECT id FROM channels") if r[0] not in {c[0] for c in ex.calls}]
    assert len(waiting) == 5
    assert subs.due_for_poll(conn, Settings().rss_poll_interval) == []     # nothing is due again straight away


async def test_the_hourly_allowance_comes_back_an_hour_later(conn):
    from newtube.jobs.scheduler import FALLBACK_PER_HOUR
    t = [1000.0]
    ex = FakeExtractor()
    sch, requests, ids = make(conn, 500, ex, clock=lambda: t[0], n_channels=FALLBACK_PER_HOUR + 1)
    for _ in range(FALLBACK_PER_HOUR + 1):
        await sch.tick()
    assert len(ex.calls) == FALLBACK_PER_HOUR
    conn.execute("UPDATE events SET ts = ts - 3601 WHERE type='channel_fallback'")      # an hour passes (the allowance is kept in the database)
    conn.execute("UPDATE channels SET rss_checked_at=NULL")                 # (pretend the waiting channels are due again)
    await sch.tick()
    assert len(ex.calls) == FALLBACK_PER_HOUR + 1


async def test_a_channel_whose_feed_is_broken_is_revisited_twice_a_day_not_every_few_hours(conn):
    from newtube.jobs.scheduler import FALLBACK_REVISIT
    ex = FakeExtractor()
    sch, requests, [cid] = make(conn, 404, ex)
    await sch.tick()
    interval = Settings().rss_poll_interval
    now = int(time.time())
    assert subs.due_for_poll(conn, interval, now=now + interval + 60) == []                  # not at the usual interval
    assert subs.due_for_poll(conn, interval, now=now + FALLBACK_REVISIT + 120) == [cid]      # but after 12 hours


def test_background_work_is_spread_over_the_day(conn):
    s = Scheduler(Settings(backfill_daily_cap=30, related_daily_cap=10), conn, http=None, extractor=None)
    assert s.pause_after("fetched") == pytest.approx(86400 / 30)                  # one about every 48 minutes
    assert s.pause_after("related") == pytest.approx(86400 / 10)                  # one about every 2.4 hours
    assert s.pause_after("polled") == 5.0 and s.pause_after("embedded") == 3.0 and s.pause_after("idle") == 300.0
    assert Scheduler(Settings(backfill_daily_cap=0), conn, http=None, extractor=None).pause_after("fetched") == 300.0
    assert Scheduler(Settings(backfill_daily_cap=100000), conn, http=None, extractor=None).pause_after("fetched") == 60.0   # never faster than a minute
    assert Scheduler(Settings(backfill_daily_cap=1), conn, http=None, extractor=None).pause_after("fetched") == 43200.0     # nor longer than 12 hours between looks


async def test_failed_channel_lookups_use_up_the_hourly_allowance_too(conn):
    """A lookup that FAILS (a bot check, a network error) is still a request: it must not be free, or a refusal would
    turn into a retry loop over every channel."""
    from newtube.jobs.scheduler import FALLBACK_PER_HOUR
    ex = FakeExtractor(fail=True)
    sch, requests, ids = make(conn, 500, ex, n_channels=FALLBACK_PER_HOUR + 6)
    for _ in range(FALLBACK_PER_HOUR + 6):
        await sch.tick()
    assert len(ex.calls) == FALLBACK_PER_HOUR


async def test_a_restart_does_not_hand_back_the_allowance(conn):
    """A new scheduler (= the server restarted) still sees this hour's lookups: the count is in the database."""
    from newtube.jobs.scheduler import FALLBACK_PER_HOUR
    ex = FakeExtractor()
    sch, requests, ids = make(conn, 500, ex, n_channels=FALLBACK_PER_HOUR + 3)
    for _ in range(FALLBACK_PER_HOUR):
        await sch.tick()
    assert len(ex.calls) == FALLBACK_PER_HOUR
    ex2 = FakeExtractor()
    restarted = Scheduler(Settings(), conn, sch.http, extractor=ex2)               # same database, brand-new process state
    conn.execute("UPDATE channels SET rss_checked_at=NULL")
    for _ in range(3):
        await restarted.tick()
    assert ex2.calls == []
