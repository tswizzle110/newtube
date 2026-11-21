"""New uploads from your most-watched channels lead Home; videos without a date or channel are backfilled first."""
import time

from newtube.jobs import backfill as bf
from tests.test_recommend import A, B, C, NOW, conn, event, ids, sub, video  # noqa: F401


def watched_a_lot(conn, cid, n=5):
    for i in range(n):
        video(conn, f"w{cid[2]}{i:010d}", cid, age_days=60 + i)
        conn.execute("INSERT INTO events(ts,type,video_id,channel_id) VALUES (?,?,?,?)",
                     (NOW - 86400, "import_playlist_add", f"w{cid[2]}{i:010d}", cid))


def test_new_uploads_from_most_watched_channels_come_first(conn):
    for c in (A, B, C):
        sub(conn, c)
    watched_a_lot(conn, A, 8)
    watched_a_lot(conn, B, 1)
    video(conn, "fresh0000a01", A, age_days=0.5)
    video(conn, "fresh0000a02", A, age_days=1.0)
    video(conn, "fresh0000a03", A, age_days=2.0)           # a third from the same channel does not lead
    video(conn, "freshc000001", C, age_days=0.1)           # brand new, but from a channel you never watch
    out = ids(conn)
    assert out[:2] == ["fresh0000a01", "fresh0000a02"]
    assert out.index("fresh0000a03") > 1
    assert out.index("freshc000001") >= 2


def test_old_uploads_do_not_lead_and_the_lead_is_labelled(conn):
    sub(conn, A)
    watched_a_lot(conn, A)
    video(conn, "stale000a001", A, age_days=30)
    from newtube.recommend.baseline import recommend
    video(conn, "fresh0000a01", A, age_days=0.5)
    recs = recommend(conn, now=NOW)
    assert recs[0].video["id"] == "fresh0000a01"
    assert "most-watched" in recs[0].reasons[0].label
    assert not any("most-watched" in r.label for r in recs[1].reasons)


def test_at_most_five_lead(conn):
    for c in (A, B, C):
        sub(conn, c); watched_a_lot(conn, c)
    for i in range(5):
        for c in (A, B, C):
            video(conn, f"f{c[2]}{i:010d}x", c, age_days=0.2 + i * 0.1)
    from newtube.recommend.baseline import recommend
    recs = recommend(conn, now=NOW)
    assert sum("most-watched" in r.reasons[0].label for r in recs) == 5


def test_undated_videos_are_backfilled_before_imported_history(conn):
    conn.execute("INSERT INTO videos(id,channel_id,title,first_seen_at,from_import) VALUES ('related0001',NULL,'t',5,0)")
    conn.execute("INSERT INTO videos(id,title,first_seen_at,from_import) VALUES ('imported0001','',1,1)")
    conn.execute("INSERT INTO events(ts,type,video_id) VALUES (1,'import_like','imported0001')")
    assert bf.next_ids(conn, NOW, limit=5)[0] == "related0001"
