"""Backups: a faithful, verified, private copy; old ones pruned; nothing else ever touched."""
import os
import sqlite3
import stat

import pytest

from newtube import backup as bk
from newtube.db.connection import connect, migrate

T0 = 1_800_000_000


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "data" / "newtube.db")
    migrate(c)
    c.execute("INSERT INTO videos(id,title,first_seen_at) VALUES ('aaaaaaaaaaa','Hello',1)")
    c.execute("INSERT INTO events(ts,type) VALUES (1,'x')")
    return c


def test_the_copy_holds_the_same_data_and_is_private(conn, tmp_path):
    dest = tmp_path / "bk"
    b = bk.make_backup(conn, dest, now=T0)
    assert b.path.name == "newtube-20270115-080000.db" and (b.events, b.videos) == (1, 1)
    copy = sqlite3.connect(b.path)
    assert copy.execute("SELECT title FROM videos").fetchone()[0] == "Hello"
    assert stat.S_IMODE(os.stat(b.path).st_mode) == 0o600 and stat.S_IMODE(os.stat(dest).st_mode) == 0o700
    assert [p.name for p in dest.iterdir()] == [b.path.name]              # no temporary file left behind


def test_a_backup_does_not_include_uncommitted_work_and_does_not_block_the_app(conn, tmp_path):
    writer = connect(tmp_path / "data" / "newtube.db")
    writer.execute("BEGIN")
    writer.execute("INSERT INTO videos(id,title,first_seen_at) VALUES ('bbbbbbbbbbb','Half-written',1)")
    b = bk.make_backup(conn, tmp_path / "bk", now=T0)
    assert b.videos == 1                                                   # the open transaction is not in it
    writer.execute("COMMIT")                                               # ...and the writer was never blocked


def test_only_the_newest_copies_are_kept_and_foreign_files_are_left_alone(conn, tmp_path):
    dest = tmp_path / "bk"
    dest.mkdir()
    (dest / "notes.txt").write_text("mine")
    (dest / "newtube-final-report.db").write_text("also mine")             # similar name, not our pattern
    made = [bk.make_backup(conn, dest, keep=3, now=T0 + i * 86400).path for i in range(5)]
    assert bk.list_backups(dest) == made[-3:]
    assert sorted(p.name for p in dest.iterdir() if p not in made) == ["newtube-final-report.db", "notes.txt"]


def test_a_failed_check_leaves_no_backup_and_removes_nothing_old(conn, tmp_path, monkeypatch):
    dest = tmp_path / "bk"
    good = bk.make_backup(conn, dest, keep=1, now=T0).path
    monkeypatch.setattr(bk, "_verify", lambda p: (_ for _ in ()).throw(bk.BackupError("corrupt")))
    with pytest.raises(bk.BackupError, match="corrupt"):
        bk.make_backup(conn, dest, keep=1, now=T0 + 86400)
    assert [p.name for p in dest.iterdir()] == [good.name]                 # the old good copy survives; no partial file


def test_keep_must_be_positive(conn, tmp_path):
    with pytest.raises(bk.BackupError):
        bk.make_backup(conn, tmp_path / "bk", keep=0)


def test_a_backup_can_be_restored_and_used(conn, tmp_path):
    b = bk.make_backup(conn, tmp_path / "bk", now=T0)
    restored = connect(b.path)
    assert restored.execute("SELECT count(*) FROM videos").fetchone()[0] == 1
    restored.execute("INSERT INTO videos(id,title,first_seen_at) VALUES ('ccccccccccc','New',2)")   # writable, migrations intact
    assert migrate(restored) == []


# ---- command line -----------------------------------------------------------------------------------

@pytest.fixture
def cli(tmp_path, monkeypatch, capsys):
    from newtube.cli import main
    monkeypatch.setenv("NEWTUBE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("NEWTUBE_CONFIG", str(tmp_path / "none.toml"))

    def run(*argv):
        code = main(["backup", *argv])
        out = capsys.readouterr()
        return code, out.out
    return run


def test_cli_backs_up_into_data_backups_by_default_and_lists(conn, cli, tmp_path):
    code, out = cli()
    assert code == 0 and "checked OK" in out and "somewhere other than this machine" in out
    assert len(list((tmp_path / "data" / "backups").glob("newtube-*.db"))) == 1
    code, out = cli("--list")
    assert code == 0 and "1 backup(s)" in out


def test_cli_dir_and_keep(conn, cli, tmp_path):
    dest = tmp_path / "elsewhere"
    code, out = cli("--dir", str(dest), "--keep", "1")
    assert code == 0 and len(list(dest.glob("newtube-*.db"))) == 1


def test_cli_without_a_database_says_so(cli, tmp_path):
    code, out = cli()
    assert code == 2 and "nothing to back up" in out
