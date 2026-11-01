import json

import pytest

from newtube import tracking
from newtube.db.connection import connect, migrate
from tests.test_api import env  # noqa: F401


def test_merge_intervals():
    assert tracking.merge_intervals([(10, 20), (0, 5), (4, 8), (20.3, 25)]) == [(0, 8), (10, 25)]
    assert tracking.merge_intervals([]) == []
    assert tracking.total_length([(0, 8), (10, 25)]) == 23


def test_sanitize_clamps_and_drops_garbage():
    raw = [[0, 50], [-5, 10], [90, 200], ["x", 1], [5], [30, 30], [40, 20]]
    assert tracking.sanitize(raw, 100) == [(0, 50), (0, 10), (90, 100)]


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    return c


def watch(conn, sid, pos, iv, **kw):
    tracking.record_watch(conn, session_id=sid, video_id="aaaaaaaaaaa", source="subs",
                          position=pos, duration=100.0, intervals=iv, ended=kw.get("ended", False),
                          now=kw.get("now", 1000))


def test_heartbeats_accumulate_into_one_session(conn):
    watch(conn, "s1", 30, [[0, 30]])
    watch(conn, "s1", 60, [[30, 60]])
    row = conn.execute("SELECT * FROM watch_sessions").fetchone()
    assert json.loads(row["intervals"]) == [[0, 60]] and row["max_position"] == 60
    assert conn.execute("SELECT count(*) FROM watch_sessions").fetchone()[0] == 1


def test_rewatch_in_new_session_does_not_double_count(conn):
    watch(conn, "s1", 50, [[0, 50]])
    watch(conn, "s2", 50, [[0, 50]])           # watched the same half again
    assert tracking.watched_fraction(conn, "aaaaaaaaaaa") == pytest.approx(0.5)
    assert conn.execute("SELECT count(*) FROM watch_sessions").fetchone()[0] == 2


def test_completion_rules_and_resume_point(conn):
    watch(conn, "s1", 40, [[0, 40]])
    p = conn.execute("SELECT * FROM video_progress").fetchone()
    assert (p["position"], p["completed"]) == (40, 0)
    watch(conn, "s1", 95, [[40, 95]], ended=True)           # 95% watched -> done
    assert conn.execute("SELECT completed FROM video_progress").fetchone()[0] == 1


def test_skipping_to_the_end_counts_as_completed_but_fraction_stays_low(conn):
    watch(conn, "s1", 99, [[0, 5], [95, 99]])
    assert conn.execute("SELECT completed FROM video_progress").fetchone()[0] == 1
    assert tracking.watched_fraction(conn, "aaaaaaaaaaa") < 0.1      # signal for "skipped to end"


def test_impressions_dedupe_per_request(conn):
    for ms in (400, 1500, 900):
        tracking.record_impression(conn, video_id="aaaaaaaaaaa", surface="subs", position=2,
                                   request_id="r1", visible_ms=ms, now=1)
    rows = conn.execute("SELECT * FROM impressions").fetchall()
    assert len(rows) == 1 and rows[0]["visible_ms"] == 1500


def test_events_endpoint_end_to_end(env):
    client, ctx, *_ = env
    body = {
        "impressions": [{"video_id": "aaaaaaaaaaa", "surface": "subs", "position": 0,
                         "request_id": "r1", "visible_ms": 1200},
                        {"video_id": "bbbbbbbbbbb", "surface": "subs", "position": 1,
                         "request_id": "r1", "visible_ms": 1100},
                        {"video_id": "bad", "surface": "subs"}],
        "clicks": [{"video_id": "aaaaaaaaaaa", "surface": "subs", "request_id": "r1", "position": 0}],
        "events": [{"type": "seek", "video_id": "aaaaaaaaaaa", "session_id": "s1",
                    "payload": {"from": 10, "to": 60}},
                   {"type": "make_it_rain", "video_id": "aaaaaaaaaaa"},
                   {"type": "pause", "payload": {"x": "y" * 5000}}],
        "watch": [{"session_id": "s1", "video_id": "aaaaaaaaaaa", "position": 60, "duration": 100,
                   "intervals": [[0, 60]]}],
    }
    r = client.post("/api/events", json=body)
    assert r.status_code == 200
    assert r.json() == {"impressions": 2, "clicks": 1, "events": 1, "watch": 1, "rejected": 3}
    c = ctx.conn
    clicked = {r["video_id"]: r["clicked"] for r in c.execute("SELECT video_id, clicked FROM impressions")}
    assert clicked == {"aaaaaaaaaaa": 1, "bbbbbbbbbbb": 0}     # ignored impression stays unclicked
    assert [r["type"] for r in c.execute("SELECT type FROM events ORDER BY id")] == ["click", "seek"]
    assert client.get("/api/video/aaaaaaaaaaa").json()["resume_position"] == 60


def test_events_requires_login(env):
    from fastapi.testclient import TestClient
    client, *_ = env
    assert TestClient(client.app).post("/api/events", json={}).status_code == 401
