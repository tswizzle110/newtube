"""The baseline recommender: what it recommends, what it never does, and that it can explain itself."""
import pytest

from newtube.db.connection import connect, migrate
from newtube.recommend.baseline import DAY, Weights, channel_interest, recommend

NOW = 1_800_000_000
A, B, C = "UC" + "a" * 22, "UC" + "b" * 22, "UC" + "c" * 22


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    for cid, name in ((A, "Alpha"), (B, "Beta"), (C, "Gamma")):
        c.execute("INSERT INTO channels(id,title,created_at) VALUES (?,?,1)", (cid, name))
    return c


def sub(conn, cid):
    conn.execute("INSERT INTO subscriptions VALUES (?,1)", (cid,))


def video(conn, vid, channel, age_days=1.0, title=None, from_import=0):
    conn.execute("INSERT INTO videos(id,channel_id,title,published_at,first_seen_at,from_import) VALUES (?,?,?,?,1,?)",
                 (vid, channel, vid if title is None else title, int(NOW - age_days * DAY), from_import))


def event(conn, typ, vid=None, ts=NOW - DAY, channel=None):
    conn.execute("INSERT INTO events(ts,type,video_id,channel_id) VALUES (?,?,?,?)", (ts, typ, vid, channel))


def ids(conn, **kw):
    return [r.video["id"] for r in recommend(conn, now=NOW, **kw)]


# ---- what gets recommended --------------------------------------------------------------------------

def test_recent_uploads_of_subscribed_channels_newest_first_when_nothing_else_is_known(conn):
    sub(conn, A)
    video(conn, "old000000a1", A, age_days=20); video(conn, "new000000a1", A, age_days=1); video(conn, "mid000000a1", A, age_days=5)
    assert ids(conn) == ["new000000a1", "mid000000a1", "old000000a1"]


def test_unsubscribed_channels_uploads_are_not_candidates(conn):
    video(conn, "nosub0000a1", A)
    assert ids(conn) == []


def test_uploads_older_than_the_window_are_not_candidates(conn):
    sub(conn, A)
    video(conn, "ancient000a1", A, age_days=200)
    assert ids(conn) == []


def test_a_channel_you_watch_a_lot_outranks_a_fresher_one_you_never_watch(conn):
    sub(conn, A); sub(conn, B)
    video(conn, "fav0000000a", A, age_days=6); video(conn, "fresh000000b", B, age_days=0.2)
    for i in range(8):
        video(conn, f"hist{i:07d}a", A, age_days=300 + i, from_import=1)
        event(conn, "import_watch", f"hist{i:07d}a")
    assert ids(conn)[0] == "fav0000000a"


def test_watch_later_items_are_candidates_even_from_unsubscribed_channels(conn):
    video(conn, "saved000000c", C, age_days=400, from_import=1)
    event(conn, "import_watch_later", "saved000000c")
    r = recommend(conn, now=NOW)
    assert [x.video["id"] for x in r] == ["saved000000c"] and any(x.label == "In your Watch later" for x in r[0].reasons)


def test_saved_videos_not_yet_filled_in_by_the_backfill_are_left_out_not_shown_blank(conn):
    video(conn, "saved000000c", None, title="", from_import=1)
    event(conn, "import_watch_later", "saved000000c")
    assert ids(conn) == []


# ---- what is never recommended ----------------------------------------------------------------------

def test_watched_finished_and_hidden_things_are_never_recommended(conn):
    sub(conn, A); sub(conn, B)
    for v in ("fine00000a1", "watched0000a", "finished000a", "hidden00000a", "banned00000b"):
        video(conn, v, A if v != "banned00000b" else B)
    event(conn, "import_watch", "watched0000a")
    conn.execute("INSERT INTO video_progress(video_id,position,duration,completed,updated_at) VALUES ('finished000a',100,100,1,1)")
    event(conn, "not_interested", "hidden00000a")
    event(conn, "dont_recommend_channel", channel=B)
    assert ids(conn) == ["fine00000a1"]


def test_something_you_started_in_newtube_comes_back_with_a_reason(conn):
    sub(conn, A)
    video(conn, "started0000a", A, age_days=2); video(conn, "other00000a1", A, age_days=2)
    conn.execute("INSERT INTO watch_sessions(id,video_id,started_at,max_position,duration) VALUES ('s','started0000a',?,50,200)", (NOW - 3600,))
    conn.execute("INSERT INTO video_progress(video_id,position,duration,completed,updated_at) VALUES ('started0000a',50,200,0,1)")
    r = {x.video["id"]: x for x in recommend(conn, now=NOW)}
    assert "started0000a" in r and any("started" in x.label for x in r["started0000a"].reasons)
    assert r["started0000a"].video["resume_position"] == 50


# ---- signals ----------------------------------------------------------------------------------------

def test_repeatedly_ignored_videos_sink(conn):
    sub(conn, A)
    video(conn, "ignored0000a", A, age_days=1); video(conn, "unseen00000a", A, age_days=1.2)
    for _ in range(6):
        conn.execute("INSERT INTO impressions(ts,video_id,surface,clicked) VALUES (?,?,?,0)", (NOW - 3600, "ignored0000a", "subs"))
    r = recommend(conn, now=NOW)
    assert [x.video["id"] for x in r] == ["unseen00000a", "ignored0000a"]
    assert any(x.label.startswith("Shown 6 times") and x.points < 0 for x in r[1].reasons)


def test_old_impressions_and_clicked_ones_do_not_count(conn):
    sub(conn, A)
    video(conn, "newer0000a1", A, age_days=0.5); video(conn, "older0000a1", A, age_days=1)
    conn.execute("INSERT INTO impressions(ts,video_id,surface,clicked) VALUES (?,?,?,0)", (NOW - 40 * DAY, "newer0000a1", "subs"))
    conn.execute("INSERT INTO impressions(ts,video_id,surface,clicked) VALUES (?,?,?,1)", (NOW - 3600, "newer0000a1", "subs"))
    assert ids(conn)[0] == "newer0000a1"


def test_interest_fades_with_time(conn):
    video(conn, "recent00000a", A, from_import=1); video(conn, "ancient0000b", B, from_import=1)
    event(conn, "import_watch", "recent00000a", ts=NOW - DAY)
    event(conn, "import_watch", "ancient0000b", ts=NOW - 900 * DAY)
    i = channel_interest(conn, NOW, 180)
    assert i[A] == 1.0 and i[B] < 0.05


def test_likes_count_for_more_than_plain_watches_and_real_playback_for_more_than_opening(conn):
    for v, ch in (("liked000000a", A), ("opened00000b", B), ("played00000c", C)):
        video(conn, v, ch, from_import=1)
    event(conn, "import_like", "liked000000a"); event(conn, "import_watch", "opened00000b")
    conn.execute("INSERT INTO watch_sessions(id,video_id,started_at,max_position,duration) VALUES ('s','played00000c',?,95,100)", (NOW - DAY,))
    i = channel_interest(conn, NOW, 180)
    assert i[A] > i[B] and i[C] > i[B]


# ---- diversity --------------------------------------------------------------------------------------

def test_one_prolific_channel_does_not_fill_the_top(conn):
    sub(conn, A); sub(conn, B)
    for n in range(5):
        video(conn, f"alpha{n:06d}a", A, age_days=0.1 + n * 0.01)
    video(conn, "beta0000000b", B, age_days=3)
    order = ids(conn)
    assert order.index("beta0000000b") < 4, order               # without the discount it would be last
    r = {x.video["id"]: x for x in recommend(conn, now=NOW)}
    assert any("already shown" in x.label and x.points < 0 for x in r["alpha000004a"].reasons)


# ---- transparency -----------------------------------------------------------------------------------

def test_the_reasons_add_up_to_the_score(conn):
    sub(conn, A)
    video(conn, "hist0000000a", A, age_days=300, from_import=1); event(conn, "import_watch", "hist0000000a")
    for n in range(3):
        video(conn, f"alpha{n:06d}a", A, age_days=1 + n)
    for rec in recommend(conn, now=NOW):
        assert rec.score == pytest.approx(sum(r.points for r in rec.reasons)), rec.reasons
        d = rec.to_dict()
        assert d["why"]["reasons"] and all(set(x) == {"label", "points"} for x in d["why"]["reasons"])


def test_the_limit_is_respected_and_the_order_is_stable(conn):
    sub(conn, A)
    for n in range(30):
        video(conn, f"vid{n:08d}a", A, age_days=1 + n * 0.5)
    first, second = ids(conn, limit=10), ids(conn, limit=10)
    assert len(first) == 10 and first == second


def test_an_empty_database_is_fine(conn):
    assert recommend(conn, now=NOW) == []


def test_changing_a_weight_changes_the_order(conn):
    sub(conn, A); sub(conn, B)
    video(conn, "fav0000000a", A, age_days=20); video(conn, "fresh000000b", B, age_days=0.1)
    video(conn, "hist0000000a", A, age_days=300, from_import=1); event(conn, "import_watch", "hist0000000a")
    assert ids(conn, weights=Weights(freshness=5.0, channel_affinity=0.1))[0] == "fresh000000b"
    assert ids(conn, weights=Weights(freshness=0.1, channel_affinity=5.0))[0] == "fav0000000a"


def test_ages_read_naturally(conn):
    from newtube.recommend.baseline import _age_text
    assert _age_text(60) == "less than an hour ago" and _age_text(3700) == "1 hour ago"
    assert _age_text(5 * 3600) == "5 hours ago" and _age_text(3 * DAY) == "3 days ago"
