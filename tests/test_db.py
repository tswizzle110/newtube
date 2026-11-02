import sqlite3

import pytest

from newtube.db.connection import connect, migrate


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    return c


def test_migrate_is_idempotent(tmp_path):
    c = connect(tmp_path / "t.db")
    from newtube.db.connection import MIGRATIONS_DIR
    every_file = sorted(f.name for f in MIGRATIONS_DIR.glob("*.sql"))
    assert migrate(c) == every_file and every_file[0] == "001_init.sql"      # all of them, in order, once
    assert migrate(c) == []


def test_wal_and_foreign_keys(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO subscriptions(channel_id, subscribed_at) VALUES ('nope', 1)")


def test_deleting_channel_cascades_subscription_but_keeps_videos(conn):
    conn.execute("INSERT INTO channels(id,title,created_at) VALUES ('UC1','c',1)")
    conn.execute("INSERT INTO subscriptions VALUES ('UC1', 1)")
    conn.execute("INSERT INTO videos(id,channel_id,title,first_seen_at) VALUES ('v1','UC1','t',1)")
    conn.execute("DELETE FROM channels WHERE id='UC1'")
    assert conn.execute("SELECT count(*) FROM subscriptions").fetchone()[0] == 0
    row = conn.execute("SELECT channel_id FROM videos WHERE id='v1'").fetchone()
    assert row["channel_id"] is None
