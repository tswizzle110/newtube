"""Liked videos, Watch later and your own playlists: one data model, logged for the recommender."""
import json

import pytest

from newtube.db import connection
from newtube.db.connection import connect, migrate
from newtube.db.queries import mylists as ml
from newtube.importers import takeout as tk
from tests import fixtures_takeout as fx

V1, V2, V3 = "aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    return c


def video(conn, vid, title="T", channel=None):
    conn.execute("INSERT OR IGNORE INTO videos(id,channel_id,title,first_seen_at) VALUES (?,?,?,1)", (vid, channel, title))


def events(conn, typ):
    return [(r["video_id"], r["payload"]) for r in conn.execute("SELECT video_id, payload FROM events WHERE type=? ORDER BY id", (typ,))]


def test_the_two_system_lists_exist_from_the_start_in_a_fixed_order(conn):
    assert [(r["kind"], r["name"], r["count"]) for r in ml.list_all(conn)] == [("liked", "Liked videos", 0), ("watch_later", "Watch later", 0)]


def test_adding_is_idempotent_and_logged_once(conn):
    video(conn, V1)
    liked = ml.resolve(conn, "liked")
    assert ml.add(conn, liked, V1, now=10) is True and ml.add(conn, liked, V1, now=20) is False
    assert events(conn, "like") == [(V1, None)] and ml.membership(conn, V1) == {"liked": True, "watch_later": False, "playlists": []}
    assert ml.remove(conn, liked, V1, now=30) is True and ml.remove(conn, liked, V1, now=40) is False
    assert events(conn, "unlike") == [(V1, None)] and ml.membership(conn, V1)["liked"] is False


def test_watch_later_logs_its_own_events(conn):
    video(conn, V1)
    wl = ml.resolve(conn, "watch_later")
    ml.add(conn, wl, V1); ml.remove(conn, wl, V1)
    assert events(conn, "watch_later_add") == [(V1, None)] and events(conn, "watch_later_remove") == [(V1, None)]


def test_system_lists_show_newest_first_with_undated_entries_last(conn):
    for v in (V1, V2, V3):
        video(conn, v, title=f"title {v[0]}")
    wl = ml.resolve(conn, "watch_later")
    ml.add(conn, wl, V1, now=100); ml.add(conn, wl, V2, now=300); ml.add(conn, wl, V3, now=0)
    assert [i["id"] for i in ml.items(conn, wl)] == [V2, V1, V3]


def test_items_carry_what_a_card_needs(conn):
    conn.execute("INSERT INTO channels(id,title,created_at) VALUES ('UC" + "a" * 22 + "','Chan',1)")
    video(conn, V1, "Hello", "UC" + "a" * 22)
    conn.execute("INSERT INTO video_progress(video_id,position,duration,completed,updated_at) VALUES (?,30,100,0,1)", (V1,))
    ml.add(conn, ml.resolve(conn, "liked"), V1)
    (i,) = ml.items(conn, ml.resolve(conn, "liked"))
    assert (i["title"], i["channel_title"], i["resume_position"], i["completed"], i["seen"]) == ("Hello", "Chan", 30, False, True)


def test_a_video_with_no_stored_details_still_lists_without_crashing(conn):
    ml.add(conn, ml.resolve(conn, "liked"), V1)
    (i,) = ml.items(conn, ml.resolve(conn, "liked"))
    assert i["id"] == V1 and i["title"] == "" and i["channel_title"] == ""


def test_unknown_lists_are_reported(conn):
    for bad in ("nope", "999", "", "-1"):
        with pytest.raises(ml.NoSuchList):
            ml.resolve(conn, bad)


def test_deleting_a_list_deletes_its_items_only(conn):
    conn.execute("INSERT INTO playlists(kind,name,created_at,updated_at) VALUES ('user','Mine',1,1)")
    mine = ml.resolve(conn, "3")
    video(conn, V1)
    ml.add(conn, mine, V1); ml.add(conn, ml.resolve(conn, "liked"), V1)
    conn.execute("DELETE FROM playlists WHERE id=3")
    assert ml.membership(conn, V1) == {"liked": True, "watch_later": False, "playlists": []}


def test_only_one_liked_and_one_watch_later_list_can_exist(conn):
    with pytest.raises(Exception):
        conn.execute("INSERT INTO playlists(kind,name,created_at,updated_at) VALUES ('liked','Again',1,1)")


# ---- the Takeout import fills the lists ---------------------------------------------------------------

def test_importing_a_takeout_puts_likes_and_watch_later_on_the_lists_once(conn, tmp_path):
    z = str(fx.make_zip(tmp_path / "t.zip"))
    rep = tk.run_import(conn, [z])
    names = {r["kind"]: r["count"] for r in ml.list_all(conn)}
    assert names["liked"] == 2 and names["watch_later"] == 1 and rep.db["list_items_new"] == 3
    again = tk.run_import(conn, [z])
    assert again.db["list_items_new"] == 0 and {r["kind"]: r["count"] for r in ml.list_all(conn)} == names
    assert "3 new entries on your Liked / Watch later lists" in tk.format_report(rep, wrote=True)


def test_a_dry_run_leaves_the_lists_empty(conn, tmp_path):
    tk.run_import(conn, [str(fx.make_zip(tmp_path / "t.zip"))], dry_run=True)
    assert all(r["count"] == 0 for r in ml.list_all(conn))


# ---- earlier imports are carried over by the migration ------------------------------------------------

def test_the_migration_carries_over_imports_made_before_lists_existed(tmp_path):
    c = connect(tmp_path / "old.db")
    files = sorted(connection.MIGRATIONS_DIR.glob("*.sql"))
    for m in files:
        if m.name.startswith("008"):
            break
        c.executescript(m.read_text())
    for vid, typ, ts in ((V1, "import_like", 500), (V1, "import_like", 100), (V2, "import_watch_later", 0), (V3, "import_watch", 5)):
        c.execute("INSERT INTO events(ts,type,video_id) VALUES (?,?,?)", (ts, typ, vid))
    c.executescript(next(m for m in files if m.name.startswith("008")).read_text())
    got = {(r["kind"], r["video_id"], r["added_at"]) for r in c.execute(
        "SELECT p.kind, i.video_id, i.added_at FROM playlist_items i JOIN playlists p ON p.id=i.playlist_id")}
    assert got == {("liked", V1, 100), ("watch_later", V2, 0)}                       # earliest date kept; plain watches are not likes


# ---- playlists you make -------------------------------------------------------------------------------

def test_create_rename_delete(conn):
    p = ml.create(conn, "  Piano   covers ")
    assert p["name"] == "Piano covers" and p["kind"] == "user"
    assert [r["name"] for r in ml.list_all(conn)] == ["Liked videos", "Watch later", "Piano covers"]
    ml.rename(conn, p, "Piano")
    assert ml.resolve(conn, str(p["id"]))["name"] == "Piano"
    video(conn, V1)
    ml.add(conn, ml.resolve(conn, str(p["id"])), V1)
    ml.delete(conn, ml.resolve(conn, str(p["id"])))
    assert conn.execute("SELECT count(*) FROM playlist_items WHERE video_id=?", (V1,)).fetchone()[0] == 0     # items go too
    assert [r["name"] for r in ml.list_all(conn)] == ["Liked videos", "Watch later"]
    assert [t for t, in conn.execute("SELECT type FROM events WHERE type LIKE 'playlist_%' ORDER BY id")] == [
        "playlist_create", "playlist_rename", "playlist_add", "playlist_delete"]


def test_names_must_be_sensible_and_unique_ignoring_case(conn):
    ml.create(conn, "Mine")
    for bad in ("", "   ", "x" * 101):
        with pytest.raises(ValueError):
            ml.create(conn, bad)
    for dup in ("mine", "MINE ", "liked videos", "Watch Later"):                  # also the two built-in names
        with pytest.raises(ml.NameTaken):
            ml.create(conn, dup)
    other = ml.create(conn, "Other")
    with pytest.raises(ml.NameTaken):
        ml.rename(conn, other, "mine")
    ml.rename(conn, other, "OTHER")                                              # renaming to itself (case change) is fine


def test_liked_and_watch_later_are_not_yours_to_change(conn):
    for ref in ("liked", "watch_later"):
        p = ml.resolve(conn, ref)
        for fn in (lambda: ml.rename(conn, p, "x"), lambda: ml.delete(conn, p), lambda: ml.reorder(conn, p, [])):
            with pytest.raises(ml.NotYours):
                fn()


def test_user_playlists_play_in_the_order_you_set(conn):
    p = ml.create(conn, "Order")
    for v in (V1, V2, V3):
        video(conn, v, v[0]); ml.add(conn, p, v)
    assert [i["id"] for i in ml.items(conn, p)] == [V1, V2, V3]                  # in the order added
    assert ml.reorder(conn, p, [V3, V1, V2]) == [V3, V1, V2]
    assert [i["id"] for i in ml.items(conn, p)] == [V3, V1, V2]
    ml.add(conn, p, "ddddddddddd")
    assert [i["id"] for i in ml.items(conn, p)][-1] == "ddddddddddd"            # new additions go last


def test_reorder_tolerates_stale_unknown_and_repeated_ids(conn):
    p = ml.create(conn, "Order")
    for v in (V1, V2, V3):
        ml.add(conn, p, v)
    assert ml.reorder(conn, p, [V2, "zzzzzzzzzzz", V2]) == [V2, V1, V3]          # unknown dropped, repeats ignored, rest kept
    assert ml.reorder(conn, p, []) == [V2, V1, V3]


def test_one_video_can_be_on_several_playlists(conn):
    a, b = ml.create(conn, "A"), ml.create(conn, "B")
    ml.add(conn, a, V1); ml.add(conn, b, V1); ml.add(conn, ml.resolve(conn, "liked"), V1)
    assert ml.membership(conn, V1) == {"liked": True, "watch_later": False, "playlists": [a["id"], b["id"]]}
    ml.remove(conn, a, V1)
    assert ml.membership(conn, V1)["playlists"] == [b["id"]]
