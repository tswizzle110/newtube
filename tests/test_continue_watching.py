"""Continue watching: what you started and did not finish, in one place; and never in the recommended grid."""
import pytest

from newtube.db.connection import connect, migrate
from newtube.db.queries import continue_watching as cw
from tests.test_api import env  # noqa: F401  (a fixture)

CH = "UC" + "a" * 22


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    c.execute("INSERT INTO channels(id,title,created_at) VALUES (?,?,1)", (CH, "Alpha"))
    return c


def started(conn, vid, position, duration=600, completed=0, updated=1000, title=None):
    conn.execute("INSERT OR IGNORE INTO videos(id,channel_id,title,duration,first_seen_at) VALUES (?,?,?,?,1)",
                 (vid, CH, title if title is not None else "T " + vid, duration))
    conn.execute("INSERT INTO video_progress(video_id,position,duration,completed,updated_at) VALUES (?,?,?,?,?)",
                 (vid, position, duration, completed, updated))


def test_started_videos_come_most_recently_watched_first(conn):
    started(conn, "old00000001", 100, updated=100)
    started(conn, "new00000001", 300, updated=300)
    started(conn, "mid00000001", 200, updated=200)
    assert [v["id"] for v in cw.started(conn)] == ["new00000001", "mid00000001", "old00000001"]


def test_finished_videos_and_stray_clicks_are_not_in_it(conn):
    started(conn, "done0000001", 590, completed=1)
    started(conn, "stray000001", 4)                                    # a few seconds in: not "started"
    started(conn, "real0000001", 10)
    assert [v["id"] for v in cw.started(conn)] == ["real0000001"]


def test_each_card_carries_what_the_page_needs(conn):
    started(conn, "real0000001", 120, duration=600)
    v = cw.started(conn)[0]
    assert (v["resume_position"], v["duration"], v["channel_title"], v["completed"], v["seen"]) == (120, 600, "Alpha", False, True)


def test_a_video_with_no_title_yet_is_left_out_and_the_list_is_capped(conn):
    started(conn, "blank000001", 100, title="")
    for i in range(5):
        started(conn, f"v{i:010d}", 100 + i, updated=i)
    assert len(cw.started(conn, limit=3)) == 3 and all(v["id"] != "blank000001" for v in cw.started(conn))


def test_mark_as_watched_finishes_it_without_watching_the_rest(conn):
    started(conn, "real0000001", 120)
    assert cw.mark_watched(conn, "real0000001") is True and cw.started(conn) == []
    assert cw.mark_watched(conn, "nothing00001") is False


def test_the_api_lists_and_marks(env):
    client, ctx, ex, seen = env
    ctx.conn.execute("INSERT OR IGNORE INTO channels(id,title,created_at) VALUES (?,?,1)", (CH, "Alpha"))
    started(ctx.conn, "real0000001", 120)
    assert [v["id"] for v in client.get("/api/continue").json()] == ["real0000001"]
    assert client.post("/api/continue/real0000001/done").json() == {"done": True}
    assert client.get("/api/continue").json() == []
    assert client.post("/api/continue/not-an-id/done").status_code == 400
    types = [r[0] for r in ctx.conn.execute("SELECT type FROM events")]
    assert "mark_watched" in types


def test_removing_from_history_takes_it_out_too(env):
    client, ctx, ex, seen = env
    ctx.conn.execute("INSERT OR IGNORE INTO channels(id,title,created_at) VALUES (?,?,1)", (CH, "Alpha"))
    started(ctx.conn, "real0000001", 120)
    ctx.conn.execute("INSERT INTO watch_sessions(id,video_id,started_at,intervals,max_position,duration) VALUES ('s','real0000001',?,'[[0,120]]',120,600)", (1000,))
    assert client.delete("/api/history/real0000001").status_code == 200
    assert client.get("/api/continue").json() == []
