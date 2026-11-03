"""Watch history: one row per video, newest first, and 'remove' that stays removed."""
import pytest

from tests.test_api import env  # noqa: F401  (the app fixture)
from newtube.db.connection import connect, migrate
from newtube.db.queries import history
from newtube.db.queries.seen import SEEN_SQL
from newtube.importers import takeout as tk
from newtube.recommend.baseline import DAY, channel_interest, recommend
from tests import fixtures_takeout as fx

NOW = 1_800_000_000
CH = "UC" + "a" * 22
V1, V2, V3 = "aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    c.execute("INSERT INTO channels(id,title,created_at) VALUES (?,?,1)", (CH, "Piano Chan"))
    return c


def video(conn, vid, title, channel=CH, published=NOW - DAY):
    conn.execute("INSERT INTO videos(id,channel_id,title,published_at,first_seen_at) VALUES (?,?,?,?,1)", (vid, channel, title, published))


def watch(conn, vid, ts, kind="import"):
    if kind == "import":
        conn.execute("INSERT INTO events(ts,type,video_id) VALUES (?,'import_watch',?)", (ts, vid))
    else:
        conn.execute("INSERT INTO watch_sessions(id,video_id,started_at,max_position,duration) VALUES (?,?,?,50,100)", (f"s{ts}{vid}", vid, ts))


def seen(conn, vid):
    return bool(conn.execute(f"SELECT {SEEN_SQL} FROM videos v LEFT JOIN video_progress p ON p.video_id=v.id WHERE v.id=?", (vid,)).fetchone()[0])


def test_one_row_per_video_newest_watch_first_mixing_imports_and_real_sessions(conn):
    for v, t in ((V1, "One"), (V2, "Two"), (V3, "Three")):
        video(conn, v, t)
    watch(conn, V1, 100); watch(conn, V1, 900); watch(conn, V2, 500, kind="session"); watch(conn, V3, 300)
    rows = history.page(conn)
    assert [(r["id"], r["watched_at"]) for r in rows] == [(V1, 900), (V2, 500), (V3, 300)]
    assert rows[0]["channel_title"] == "Piano Chan" and rows[0]["seen"] is True


def test_paging_does_not_repeat_or_skip(conn):
    for i in range(7):
        video(conn, f"vid{i:08d}", f"t{i}"); watch(conn, f"vid{i:08d}", 100 + i)
    got = [r["id"] for off in (0, 3, 6) for r in history.page(conn, 3, off)]
    assert got == [f"vid{i:08d}" for i in range(6, -1, -1)]


def test_a_watched_video_we_know_nothing_else_about_still_lists(conn):
    watch(conn, V1, 100)
    (r,) = history.page(conn)
    assert r["id"] == V1 and r["title"] == "" and r["channel_title"] == ""


def test_search_by_title_or_channel_treats_wildcards_literally(conn):
    conn.execute("INSERT INTO channels(id,title,created_at) VALUES ('UC" + "b" * 22 + "','Other',1)")
    video(conn, V1, "100% Piano", channel="UC" + "b" * 22); video(conn, V2, "Guitar lesson", channel="UC" + "b" * 22)
    video(conn, V3, "under_score")                                        # on the channel "Piano Chan"
    for i, v in enumerate((V1, V2, V3)):
        watch(conn, v, 100 + i)
    ids = lambda q: {r["id"] for r in history.page(conn, q=q)}            # noqa: E731
    assert ids("guitar") == {V2} and ids("PIANO") == {V1, V3}             # title (V1) or channel name (V3), any case
    assert ids("100%") == {V1} and ids("%") == {V1}                       # % is just a character
    assert ids("_") == {V3} and ids("nothing like this") == set()


# ---- removing ---------------------------------------------------------------------------------------

def test_removing_hides_it_forgets_progress_and_logs(conn):
    video(conn, V1, "One"); video(conn, V2, "Two")
    watch(conn, V1, 100); watch(conn, V2, 200)
    conn.execute("INSERT INTO video_progress(video_id,position,duration,completed,updated_at) VALUES (?,30,100,0,1)", (V1,))
    assert seen(conn, V1)
    history.remove(conn, V1, now=1000)
    assert [r["id"] for r in history.page(conn)] == [V2]
    assert not seen(conn, V1) and conn.execute("SELECT count(*) FROM video_progress").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM events WHERE type='history_remove' AND video_id=?", (V1,)).fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM events WHERE type='import_watch'").fetchone()[0] == 2     # the raw log is untouched


def test_watching_again_after_removing_brings_it_back(conn):
    video(conn, V1, "One")
    watch(conn, V1, 100); history.remove(conn, V1, now=1000)
    assert history.page(conn) == []
    watch(conn, V1, 2000, kind="session")
    assert [r["id"] for r in history.page(conn)] == [V1] and seen(conn, V1)


def test_removing_twice_keeps_the_latest_cutoff(conn):
    video(conn, V1, "One")
    watch(conn, V1, 100); history.remove(conn, V1, now=500)
    watch(conn, V1, 600); history.remove(conn, V1, now=700)
    assert history.page(conn) == []


def test_importing_a_newer_export_cannot_resurrect_what_you_removed(conn, tmp_path):
    z1 = str(fx.make_zip(tmp_path / "a.zip"))
    tk.run_import(conn, [z1])
    first = history.page(conn, 100)
    gone = first[0]["id"]
    history.remove(conn, gone)                                         # "now" is later than every imported watch
    extra = [fx.watch("zzzzzzzzzzz", "2026-11-02T00:00:00Z", "Brand new", ("Chan B", fx.UC2))] + fx.WATCH_HISTORY
    tk.run_import(conn, [str(fx.make_zip(tmp_path / "b.zip", watch_history=extra))])
    ids = [r["id"] for r in history.page(conn, 100)]
    assert gone not in ids                                             # the removed one stays removed...
    assert "zzzzzzzzzzz" in ids and len(ids) == len(first)             # ...while a genuinely new video from the new export shows


def test_removed_watches_stop_counting_for_the_recommender(conn):
    conn.execute("INSERT INTO subscriptions VALUES (?,1)", (CH,))
    video(conn, V1, "old favourite", published=NOW - 300 * DAY)
    watch(conn, V1, NOW - DAY)
    assert channel_interest(conn, NOW, 180) == {CH: 1.0}
    history.remove(conn, V1, now=NOW - 3600)
    assert channel_interest(conn, NOW, 180) == {}


def test_a_removed_video_can_be_recommended_again(conn):
    conn.execute("INSERT INTO subscriptions VALUES (?,1)", (CH,))
    video(conn, V1, "fresh upload", published=NOW - 3600)
    watch(conn, V1, NOW - 60)
    assert recommend(conn, now=NOW) == []                              # watched: not recommended
    history.remove(conn, V1, now=NOW - 30)
    assert [r.video["id"] for r in recommend(conn, now=NOW)] == [V1]   # forgotten: eligible again


# ---- the API ----------------------------------------------------------------------------------------

def test_history_api(env):
    client, ctx, _ex, _seen = env
    ctx.conn.execute("INSERT INTO channels(id,title,created_at) VALUES (?,?,1)", (CH, "Piano Chan"))
    video(ctx.conn, V1, "One"); watch(ctx.conn, V1, 100)
    assert [r["id"] for r in client.get("/api/history").json()] == [V1]
    assert client.get("/api/history?q=nomatch").json() == []
    assert client.delete(f"/api/history/{V1}").json() == {"removed": True}
    assert client.get("/api/history").json() == []
    assert client.delete("/api/history/bad").status_code == 400


def test_history_knows_the_length_from_progress_when_the_video_itself_is_not_stored(conn):
    watch(conn, V1, 100, kind="session")                                  # no videos row at all
    conn.execute("INSERT INTO video_progress(video_id,position,duration,completed,updated_at) VALUES (?,600,1200,0,1)", (V1,))
    (r,) = history.page(conn)
    assert (r["duration"], r["resume_position"], r["completed"]) == (1200, 600, False)


def test_a_half_watched_video_is_not_marked_finished(conn):
    from newtube import tracking
    video(conn, V1, "Long one")
    tracking.record_watch(conn, session_id="s1", video_id=V1, source="direct", position=600.0, duration=1200.0,
                          intervals=[(0.0, 600.0)], ended=True)
    (r,) = history.page(conn)
    assert r["completed"] is False and r["resume_position"] == 600
    tracking.record_watch(conn, session_id="s2", video_id=V1, source="direct", position=1195.0, duration=1200.0,
                          intervals=[(600.0, 1195.0)], ended=True)
    assert history.page(conn)[0]["completed"] is True
