"""Reading a Takeout and importing it: exact numbers, idempotence, rollback, protection of existing data."""
import json
import time
import zipfile

import pytest

from newtube.db.connection import connect, migrate
from newtube.importers import takeout as tk
from newtube.importers.parse import parse_time
from tests import fixtures_takeout as fx


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    return c


@pytest.fixture(params=["zip", "folder"])
def export(request, tmp_path):
    """The same export as a .zip and as an unpacked folder: both must behave identically."""
    return str(fx.make_zip(tmp_path / "takeout.zip") if request.param == "zip" else fx.make_folder(tmp_path / "unpacked"))


def count(conn, sql, *args):
    return conn.execute(sql, args).fetchone()[0]


def types(conn):
    return dict(conn.execute("SELECT type, count(*) FROM events GROUP BY type").fetchall())


# ---- inspect (reads, writes nothing) -----------------------------------------------------------------

def test_inspect_reports_exact_numbers_and_why_things_were_skipped(export):
    r = tk.read_takeout([export], tk.NullSink())
    assert (r.watches, len(r.videos), len(r.channels), r.searches) == (fx.KEPT_WATCHES, fx.KEPT_VIDEOS, 2, 3)
    assert dict(r.skipped) == {"removed": 1, "ad": 1, "post": 1, "unavailable": 1, "music": 1, "no_time": 1,
                               "other_product": 1, "other_url": 1}
    assert (r.first_watch, r.last_watch) == (parse_time("2026-09-06T00:00:00Z"), parse_time("2026-10-30T12:00:00Z"))
    assert r.subscriptions == 3 and r.index_playlists == 3
    assert sorted(r.playlists) == [("Liked videos", "likes", 2), ("My mix", "other", 2), ("Watch later", "watch_later", 1)]
    assert dict(r.ignored) == {".html": 1, ".mp4": 1}


def test_inspect_lists_files_it_could_not_use_without_printing_their_content(export):
    r = tk.read_takeout([export], tk.NullSink())
    odd = {f.name.rsplit("/", 1)[-1]: f for f in r.files if f.kind == "unrecognised"}
    assert set(odd) == {"profile.json", "library.csv"}
    note = odd["profile.json"].note
    assert '"customUrl": "str"' in note and '"y"' not in note                           # key names and types, never values
    text = tk.format_report(r)
    assert "Files I could not use" in text and "profile.json" in text
    assert "Newest watch" not in text and "cats playing piano" not in text                    # no history content in the report


def test_the_printed_report_names_the_important_numbers(export):
    text = tk.format_report(tk.read_takeout([export], tk.NullSink()))
    for needle in ("7  (6 different videos, 2 channels", "searches: 3", "Subscriptions: 3 channels", "LIKES", "WATCH LATER",
                   "1 removed videos", "1 ads", "1 YouTube Music", "2026-09-06 to 2026-10-30"):
        assert needle in text, needle


# ---- importing --------------------------------------------------------------------------------------

def test_import_writes_exactly_what_the_report_says(conn, export):
    rep = tk.run_import(conn, [export])
    assert types(conn) == {"import_watch": 7, "import_search": 3, "import_like": 2, "import_watch_later": 1,
                           "import_playlist_add": 2, "subscribe": 3}
    assert count(conn, "SELECT count(*) FROM videos") == 9          # 6 watched + liked + watch-later + mix-only (aaaa is shared)
    assert count(conn, "SELECT count(*) FROM channels") == 3 and count(conn, "SELECT count(*) FROM subscriptions") == 3
    assert rep.db["videos_new"] == 9 and rep.db["channels_new"] == 3 and rep.db["subscriptions_new"] == 3
    assert rep.db["events_new"] == rep.db["events_seen"] == 15       # 7 + 3 + 2 + 1 + 2 (the 3 'subscribe' events are separate)


def test_what_is_stored_for_each_kind_of_record(conn, export):
    tk.run_import(conn, [export])
    v = {r["id"]: r for r in conn.execute("SELECT * FROM videos")}
    assert v["aaaaaaaaaaa"]["title"] == "Newest watch" and v["aaaaaaaaaaa"]["channel_id"] == fx.UC1
    assert v["ggggggggggg"]["channel_id"] is None and v["lllllllllll"]["title"] == ""             # unknown things stay unknown
    assert all(r["detail_fetched_at"] is None and r["published_at"] is None for r in v.values())  # still to be backfilled
    watches = [r["ts"] for r in conn.execute("SELECT ts FROM events WHERE type='import_watch' AND video_id='aaaaaaaaaaa' ORDER BY ts")]
    assert watches == [parse_time("2026-10-01T09:30:00.123Z"), parse_time("2026-10-30T12:00:00Z")]  # the rewatch is kept
    like = conn.execute("SELECT ts FROM events WHERE type='import_like' AND video_id='aaaaaaaaaaa'").fetchone()
    assert like["ts"] == parse_time("2026-08-01T10:00:00+00:00")
    mix = conn.execute("SELECT payload FROM events WHERE type='import_playlist_add' AND video_id='nnnnnnnnnnn'").fetchone()
    assert json.loads(mix["payload"]) == {"playlist": "My mix", "src": "takeout"}
    q = sorted(json.loads(r["payload"])["q"] for r in conn.execute("SELECT payload FROM events WHERE type='import_search'"))
    assert q == ["café 中", "cats playing piano", "cats playing piano"]
    ch = {r["id"]: r["title"] for r in conn.execute("SELECT id, title FROM channels")}
    assert ch == {fx.UC1: "Chan A", fx.UC2: "Chan B, the \"second\" one", fx.UC3: "Chan C"}      # subscription titles win
    sub = conn.execute("SELECT payload FROM events WHERE type='subscribe'").fetchone()
    assert json.loads(sub["payload"]) == {"source": "takeout"}


def test_imported_history_is_not_a_tracked_watch_session(conn, export):
    tk.run_import(conn, [export])
    for table in ("watch_sessions", "video_progress", "impressions"):
        assert count(conn, f"SELECT count(*) FROM {table}") == 0, table              # history has no completion data: don't fake it


def test_importing_twice_adds_nothing(conn, export):
    first = tk.run_import(conn, [export])
    snapshot = {t: count(conn, f"SELECT count(*) FROM {t}") for t in ("events", "videos", "channels", "subscriptions")}
    second = tk.run_import(conn, [export])
    assert {t: count(conn, f"SELECT count(*) FROM {t}") for t in snapshot} == snapshot
    assert (second.db["events_new"], second.db["videos_new"], second.db["channels_new"], second.db["subscriptions_new"]) == (0, 0, 0, 0)
    assert second.db["events_seen"] == first.db["events_seen"]


def test_a_newer_overlapping_export_adds_only_what_is_new(conn, tmp_path):
    tk.run_import(conn, [str(fx.make_zip(tmp_path / "old.zip"))])
    newer = [fx.watch("zzzzzzzzzzz", "2026-11-02T00:00:00Z", "Brand new", ("Chan B", fx.UC2))] + fx.WATCH_HISTORY
    rep = tk.run_import(conn, [str(fx.make_zip(tmp_path / "new.zip", watch_history=newer))])
    assert rep.db["events_new"] == 1 and rep.db["videos_new"] == 1 and rep.db["channels_new"] == 0


def test_a_dry_run_reports_the_same_numbers_and_keeps_nothing(conn, export):
    dry = tk.run_import(conn, [export], dry_run=True)
    assert all(count(conn, f"SELECT count(*) FROM {t}") == 0 for t in ("events", "videos", "channels", "subscriptions"))
    real = tk.run_import(conn, [export])
    assert dry.db == real.db
    assert "Dry run (nothing was saved)" in tk.format_report(dry, wrote=False) and "Imported" in tk.format_report(real, wrote=True)


def test_a_failure_halfway_leaves_the_database_untouched(conn, export, monkeypatch):
    def boom(self):
        raise RuntimeError("disk full, say")
    monkeypatch.setattr(tk.DbSink, "finish", boom)
    with pytest.raises(RuntimeError):
        tk.run_import(conn, [export])
    assert all(count(conn, f"SELECT count(*) FROM {t}") == 0 for t in ("events", "videos", "channels", "subscriptions"))
    assert not conn.in_transaction


def test_batching_does_not_change_the_result(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(tk.DbSink, "BATCH", 2)                                           # flush every other record
    tk.run_import(conn, [str(fx.make_zip(tmp_path / "t.zip"))])
    assert types(conn) == {"import_watch": 7, "import_search": 3, "import_like": 2, "import_watch_later": 1,
                           "import_playlist_add": 2, "subscribe": 3}


def test_existing_real_data_is_never_overwritten(conn, export):
    conn.execute("INSERT INTO channels(id,title,created_at) VALUES (?,?,1)", (fx.UC1, "Real Name"))
    conn.execute("INSERT INTO subscriptions VALUES (?,5)", (fx.UC1,))
    conn.execute("INSERT INTO videos(id,channel_id,title,description,duration,first_seen_at,detail_fetched_at)"
                 " VALUES ('aaaaaaaaaaa',?,'Real title','real description',99,1,5)", (fx.UC1,))
    rep = tk.run_import(conn, [export])
    assert conn.execute("SELECT title FROM channels WHERE id=?", (fx.UC1,)).fetchone()[0] == "Real Name"
    v = conn.execute("SELECT title, description, duration, detail_fetched_at FROM videos WHERE id='aaaaaaaaaaa'").fetchone()
    assert tuple(v) == ("Real title", "real description", 99, 5)
    assert conn.execute("SELECT subscribed_at FROM subscriptions WHERE channel_id=?", (fx.UC1,)).fetchone()[0] == 5
    assert rep.db["subscriptions_new"] == 2 and count(conn, "SELECT count(*) FROM events WHERE type='subscribe'") == 2


# ---- options ----------------------------------------------------------------------------------------

def test_skip_leaves_parts_out_but_the_report_still_describes_them(conn, export):
    rep = tk.run_import(conn, [export], tk.Options(skip={"watch", "likes", "subscriptions"}))
    t = types(conn)
    assert "import_watch" not in t and "import_like" not in t and "subscribe" not in t
    assert t["import_search"] == 3 and t["import_watch_later"] == 1
    assert rep.watches == 7 and count(conn, "SELECT count(*) FROM subscriptions") == 0


def test_music_is_counted_only_when_asked_for(conn, tmp_path):
    z = str(fx.make_zip(tmp_path / "t.zip"))
    assert tk.read_takeout([z], tk.NullSink()).watches == 7
    assert tk.read_takeout([z], tk.NullSink(), tk.Options(include_music=True)).watches == 8


def test_likes_and_watch_later_can_be_named_when_detection_cannot_know(conn, tmp_path):
    files = {k.replace("Liked videos", "Playlist A").replace("Watch later", "Playlist B"): v for k, v in fx.files().items()}
    z = tmp_path / "odd.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    plain = tk.read_takeout([str(z)], tk.NullSink())
    assert "no liked-videos file recognised" in tk.format_report(plain)
    named = tk.read_takeout([str(z)], tk.NullSink(), tk.Options(likes="Playlist A", watch_later="playlist b"))
    assert ("Playlist A", "likes", 2) in named.playlists and ("Playlist B", "watch_later", 1) in named.playlists


def test_a_spanish_export_works_without_any_english_names(conn, tmp_path):
    z = str(fx.make_zip(tmp_path / "es.zip", language="es"))
    rep = tk.run_import(conn, [z])
    assert (rep.watches, rep.subscriptions) == (7, 3)
    assert sorted(c for _, c, _ in rep.playlists) == ["likes", "other", "watch_later"]
    assert types(conn)["import_like"] == 2 and types(conn)["import_watch_later"] == 1 and types(conn)["subscribe"] == 3


# ---- hostile and damaged input ----------------------------------------------------------------------

def test_a_zip_with_path_traversal_names_is_only_read_never_written(tmp_path):
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../../evil.json", json.dumps(fx.WATCH_HISTORY[:2]))
        zf.writestr("/abs/evil2.csv", "a,b\n")
    rep = tk.read_takeout([str(z)], tk.NullSink())
    assert rep.watches == 2                                                          # read like any other file
    assert not (tmp_path.parent / "evil.json").exists() and not (tmp_path / ".." / ".." / "evil.json").exists()
    assert not list(tmp_path.glob("*.json"))


def test_a_damaged_json_file_is_reported_and_the_rest_is_still_used(conn, tmp_path):
    z = tmp_path / "t.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for name, text in fx.files().items():
            zf.writestr(name, text)
        zf.writestr("Takeout/more/watch-history.json", json.dumps(fx.WATCH_HISTORY[:2])[:-60])        # cut off mid-entry
    rep = tk.run_import(conn, [str(z)])
    broken = [f for f in rep.files if f.kind == "broken"]
    assert len(broken) == 1 and "invalid JSON" in broken[0].note and "1 entries before the damage" in broken[0].note
    assert "Files I could not use" in tk.format_report(rep) and rep.subscriptions == 3


def test_a_missing_path_is_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="no such file"):
        tk.read_takeout([str(tmp_path / "nope.zip")], tk.NullSink())


def test_several_zips_are_combined(conn, tmp_path):
    a, b = tmp_path / "a.zip", tmp_path / "b.zip"
    with zipfile.ZipFile(a, "w") as z:
        z.writestr(fx.ROOT + "history/watch-history.json", json.dumps(fx.WATCH_HISTORY))
    with zipfile.ZipFile(b, "w") as z:
        z.writestr(fx.ROOT + "subscriptions/subscriptions.csv", fx.csv_text(fx.SUBSCRIPTIONS))
    rep = tk.run_import(conn, [str(a), str(b)])
    assert rep.watches == 7 and rep.subscriptions == 3


def test_a_big_history_streams_and_imports_completely(conn, tmp_path):
    n = 20_000
    entries = [fx.watch(f"v{i:010d}", time.strftime("2026-01-01T00:00:00Z", time.gmtime(0)).replace("00:00:00", f"{i // 3600 % 24:02d}:{i // 60 % 60:02d}:{i % 60:02d}"),
                        f"Video {i}", (f"Chan {i % 50}", "UC" + f"{i % 50:022d}")) for i in range(n)]
    for i, e in enumerate(entries):                                                  # make every (video, time) pair distinct
        e["time"] = f"2026-{1 + i % 12:02d}-{1 + i % 28:02d}T{i % 24:02d}:{i // 24 % 60:02d}:{i % 60:02d}Z"
    z = fx.make_zip(tmp_path / "big.zip", watch_history=entries)
    t0 = time.time()
    rep = tk.run_import(conn, [str(z)])
    assert rep.watches == n and len(rep.videos) == n and len(rep.channels) == 50
    assert count(conn, "SELECT count(*) FROM events WHERE type='import_watch'") == n and time.time() - t0 < 20


# ---- command line ------------------------------------------------------------------------------------

@pytest.fixture
def cli(tmp_path, monkeypatch, capsys):
    from newtube.cli import main
    monkeypatch.setenv("NEWTUBE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("NEWTUBE_CONFIG", str(tmp_path / "none.toml"))

    def run(*argv):
        code = main(["takeout", *argv])
        out = capsys.readouterr()
        return code, out.out, out.err
    return run


def _db_rows(tmp_path):
    from newtube.config import load_settings
    c = connect(load_settings().db_path)
    try:
        return count(c, "SELECT count(*) FROM events")
    finally:
        c.close()


def test_cli_inspect_prints_the_report_and_creates_no_database(cli, tmp_path):
    z = str(fx.make_zip(tmp_path / "t.zip"))
    code, out, _ = cli("inspect", z)
    assert code == 0 and "Subscriptions: 3 channels" in out
    assert not (tmp_path / "data").exists()


def test_cli_dry_run_then_import_then_import_again(cli, tmp_path):
    z = str(fx.make_zip(tmp_path / "t.zip"))
    code, out, _ = cli("import", z, "--dry-run")
    assert code == 0 and "Dry run (nothing was saved)" in out and _db_rows(tmp_path) == 0
    code, out, _ = cli("import", z)
    assert code == 0 and "Imported" in out and "nothing is imported twice" in out and _db_rows(tmp_path) == 18
    code, out, _ = cli("import", z)
    assert code == 0 and _db_rows(tmp_path) == 18


def test_cli_options_are_honoured(cli, tmp_path):
    z = str(fx.make_zip(tmp_path / "t.zip"))
    assert cli("import", z, "--skip", "watch,search,likes,watch-later,playlists")[0] == 0
    assert _db_rows(tmp_path) == 3                                                       # only the 3 subscriptions
    assert cli("import", z, "--include-music", "--skip", "search,subscriptions,likes,watch-later,playlists")[0] == 0
    assert _db_rows(tmp_path) == 3 + 8


def test_cli_rejects_unknown_parts_and_missing_files(cli, tmp_path):
    z = str(fx.make_zip(tmp_path / "t.zip"))
    code, out, _ = cli("import", z, "--skip", "history")
    assert code == 2 and "Unknown part(s) to skip: history" in out
    code, out, _ = cli("inspect", str(tmp_path / "missing.zip"))
    assert code == 2 and "no such file" in out
