"""The count of new uploads beside Subscriptions: what arrived since you last looked, unwatched, from your subscriptions."""
import pytest

from newtube.db.connection import connect, migrate
from newtube.db.queries import subscriptions as subs
from newtube.extract.base import ChannelInfo
from tests.test_api import env  # noqa: F401  (a fixture)

CH, OTHER = "UC" + "a" * 22, "UC" + "b" * 22
NOW = 1_800_000_000


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    subs.subscribe(c, ChannelInfo(CH, "Alpha"))
    c.execute("INSERT INTO channels(id,title,created_at) VALUES (?,?,1)", (OTHER, "Other"))
    return c


def upload(conn, vid, first_seen, published=None, channel=CH, title="T", imported=0):
    conn.execute("INSERT INTO videos(id,channel_id,title,published_at,first_seen_at,from_import) VALUES (?,?,?,?,?,?)",
                 (vid, channel, title, published if published is not None else first_seen, first_seen, imported))


def test_the_first_look_starts_from_now_so_nothing_old_is_announced(conn):
    upload(conn, "old00000001", NOW - 100)
    assert subs.new_count(conn, NOW) == 0
    upload(conn, "new00000001", NOW + 50)
    assert subs.new_count(conn, NOW + 100) == 1


def test_only_unwatched_uploads_from_subscriptions_count(conn):
    subs.mark_seen(conn, NOW)
    upload(conn, "good0000001", NOW + 10)
    upload(conn, "other000001", NOW + 10, channel=OTHER)                       # not a subscription
    upload(conn, "import00001", NOW + 10, imported=1)                           # a Takeout row, not an upload
    upload(conn, "watched0001", NOW + 10)
    conn.execute("INSERT INTO video_progress(video_id,position,duration,completed,updated_at) VALUES ('watched0001',30,100,0,1)")
    upload(conn, "blank000001", NOW + 10, title="")
    assert subs.new_count(conn, NOW + 100) == 1


def test_opening_subscriptions_resets_the_count(conn):
    subs.mark_seen(conn, NOW)
    upload(conn, "a0000000001", NOW + 10); upload(conn, "a0000000002", NOW + 20)
    assert subs.new_count(conn, NOW + 100) == 2
    subs.mark_seen(conn, NOW + 100)
    assert subs.new_count(conn, NOW + 101) == 0
    upload(conn, "a0000000003", NOW + 200)
    assert subs.new_count(conn, NOW + 300) == 1


def test_an_upload_older_than_two_weeks_is_never_new_even_if_just_discovered(conn):
    subs.mark_seen(conn, NOW)
    upload(conn, "ancient0001", NOW + 10, published=NOW - 20 * 86400)         # a back-catalogue video found late
    assert subs.new_count(conn, NOW + 100) == 0


def test_the_api(env):
    client, ctx, ex, seen = env
    assert client.get("/api/subscriptions/new").json() == {"count": 0}
    assert client.post("/api/subscriptions/seen").json() == {"ok": True}
