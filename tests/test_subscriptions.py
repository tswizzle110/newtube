import httpx
import pytest

from newtube.config import Settings
from newtube.db.connection import connect, migrate
from newtube.db.queries import subscriptions as subs
from newtube.extract.base import ChannelInfo, ExtractionError
from newtube.extract.rss import parse_feed
from newtube.jobs.scheduler import Scheduler
from newtube.net.http import OutboundClient
from newtube.net.ratelimit import RateLimiter
from tests.fixtures_rss import SAMPLE_FEED

CID = "UCBR8-60-B28hp2BmDPdntcQ"


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    return c


def test_parse_feed():
    f = parse_feed(SAMPLE_FEED)
    assert (f.channel_id, f.channel_title, len(f.entries)) == (CID, "YouTube", 2)
    e = f.entries[0]
    assert (e.video_id, e.title, e.view_count) == ("aaaaaaaaaaa", "Newest & best", 1234)
    assert e.published_at == 1793361600
    assert f.entries[1].thumbnail_url is None


def test_parse_feed_rejects_garbage():
    with pytest.raises(ExtractionError):
        parse_feed("<html>not a feed</html>")
    with pytest.raises(ExtractionError):
        parse_feed("not xml at all")


def test_subscribe_logs_events_and_is_idempotent(conn):
    ch = ChannelInfo(CID, "YouTube", "@YouTube")
    assert subs.subscribe(conn, ch) is True
    assert subs.subscribe(conn, ch) is False
    assert subs.unsubscribe(conn, CID) is True
    types = [r["type"] for r in conn.execute("SELECT type FROM events ORDER BY id")]
    assert types == ["subscribe", "unsubscribe"]


def test_store_feed_counts_new_and_keeps_detail(conn):
    subs.subscribe(conn, ChannelInfo(CID, "YouTube"))
    feed = parse_feed(SAMPLE_FEED)
    assert subs.store_feed(conn, feed) == 2
    conn.execute("UPDATE videos SET duration=99, detail_fetched_at=5 WHERE id='aaaaaaaaaaa'")
    assert subs.store_feed(conn, feed) == 0
    row = conn.execute("SELECT duration FROM videos WHERE id='aaaaaaaaaaa'").fetchone()
    assert row["duration"] == 99                       # RSS refresh didn't wipe extractor data


def test_feed_is_chronological_and_only_subscribed(conn):
    subs.subscribe(conn, ChannelInfo(CID, "YouTube"))
    subs.store_feed(conn, parse_feed(SAMPLE_FEED))
    other = ChannelInfo("UC" + "x" * 22, "Other")
    subs.upsert_channel(conn, other)          # known channel, NOT subscribed
    conn.execute("INSERT INTO videos(id,channel_id,title,published_at,first_seen_at)"
                 " VALUES ('zzzzzzzzzzz',?, 't', 2000000000, 1)", (other.id,))
    ids = [r["id"] for r in subs.get_feed(conn)]
    assert ids == ["aaaaaaaaaaa", "bbbbbbbbbbb"]
    older = subs.get_feed(conn, before=subs.get_feed(conn)[0]["published_at"])
    assert [r["id"] for r in older] == ["bbbbbbbbbbb"]


def test_due_for_poll_orders_never_polled_first(conn):
    a, b = ChannelInfo("UC" + "a" * 22, "A"), ChannelInfo("UC" + "b" * 22, "B")
    subs.subscribe(conn, a); subs.subscribe(conn, b)
    conn.execute("UPDATE channels SET rss_checked_at=1000 WHERE id=?", (a.id,))
    assert subs.due_for_poll(conn, 3600, now=5000) == [b.id, a.id]   # never-polled B first
    assert subs.due_for_poll(conn, 3600, now=1500) == [b.id]     # a was polled recently


async def test_scheduler_polls_one_channel_per_tick_and_survives_errors(conn):
    subs.subscribe(conn, ChannelInfo(CID, "YouTube"))
    subs.subscribe(conn, ChannelInfo("UC" + "b" * 22, "Broken"))

    def handler(req):
        if CID in str(req.url):
            return httpx.Response(200, text=SAMPLE_FEED)
        return httpx.Response(500)

    http = OutboundClient(Settings(), RateLimiter(0, 0, 100), transport=httpx.MockTransport(handler))
    sch = Scheduler(Settings(), conn, http)
    assert await sch.tick() == "polled"
    assert await sch.tick() == "polled"          # the broken one: logged, marked, not fatal
    assert await sch.tick() == "idle"            # both now count as checked
    assert conn.execute("SELECT count(*) FROM videos").fetchone()[0] == 2


def test_feed_header_id_without_uc_prefix_is_normalised():
    """Regression: real feeds report the header channel id as 22 chars (no 'UC'); using it
    directly broke the foreign key on every poll."""
    f = parse_feed(SAMPLE_FEED)
    assert f.channel_id == CID and len(f.channel_id) == 24


async def test_poll_stores_under_requested_channel_id_even_if_feed_disagrees(conn):
    subs.subscribe(conn, ChannelInfo(CID, "YouTube"))
    odd = SAMPLE_FEED.replace(
        "<yt:channelId>BR8-60-B28hp2BmDPdntcQ</yt:channelId>", "<yt:channelId>SomethingElseEntirely</yt:channelId>")
    http = OutboundClient(Settings(), RateLimiter(0, 0, 100),
                          transport=httpx.MockTransport(lambda r: httpx.Response(200, text=odd)))
    from newtube.jobs.scheduler import poll_channel
    assert await poll_channel(conn, http, CID) == 2
    assert conn.execute("SELECT DISTINCT channel_id FROM videos").fetchall()[0][0] == CID


async def test_failed_poll_retries_in_ten_minutes_not_hours(conn):
    subs.subscribe(conn, ChannelInfo(CID, "YouTube"))
    http = OutboundClient(Settings(), RateLimiter(0, 0, 100),
                          transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    s = Settings()
    sch = Scheduler(s, conn, http)
    assert await sch.tick() == "polled"
    import time
    checked = conn.execute("SELECT rss_checked_at FROM channels").fetchone()[0]
    due_in = checked + s.rss_poll_interval - time.time()
    assert 590 <= due_in <= 610
    assert subs.due_for_poll(conn, s.rss_poll_interval, now=int(time.time()) + 700) == [CID]
