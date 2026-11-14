"""The play queue: order, no duplicates, Play next, reordering, and the API."""
import pytest

from newtube.db.connection import connect, migrate
from newtube.db.queries import queue as q
from tests.test_api import env  # noqa: F401  (a fixture)

V1, V2, V3, V4 = "aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc", "ddddddddddd"
CH = "UC" + "a" * 22


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    return c


def order(conn):
    return [v["id"] for v in q.items(conn)]


def test_videos_play_in_the_order_they_were_added(conn):
    for v in (V1, V2, V3):
        assert q.add(conn, v) is True
    assert order(conn) == [V1, V2, V3] and q.count(conn) == 3


def test_a_video_already_queued_is_not_duplicated(conn):
    q.add(conn, V1); q.add(conn, V2)
    assert q.add(conn, V1) is False and order(conn) == [V1, V2]


def test_play_next_goes_in_front_and_moves_an_already_queued_video_there(conn):
    q.add(conn, V1); q.add(conn, V2)
    assert q.add(conn, V3, front=True) is True and order(conn) == [V3, V1, V2]
    assert q.add(conn, V2, front=True) is True and order(conn) == [V2, V3, V1]
    assert q.add(conn, V2, front=True) is False                     # already first: nothing to do
    q.add(conn, V4, front=True)
    assert order(conn)[0] == V4


def test_remove_reorder_and_clear(conn):
    for v in (V1, V2, V3):
        q.add(conn, v)
    assert q.remove(conn, V2) is True and q.remove(conn, V2) is False and order(conn) == [V1, V3]
    q.reorder(conn, [V3, V1])
    assert order(conn) == [V3, V1]
    q.reorder(conn, [V1, "zzzzzzzzzzz"])                            # unknown ids are ignored; unnamed ones keep their order after
    assert order(conn) == [V1, V3]
    assert q.clear(conn) == 2 and order(conn) == []


def test_a_video_without_stored_details_is_still_listed(conn):
    q.add(conn, V1)
    item = q.items(conn)[0]
    assert item["id"] == V1 and item["title"] == "" and item["seen"] is False


def test_the_api(env):
    client, ctx, ex, seen = env
    assert client.get("/api/queue").json() == {"count": 0, "videos": []}
    assert client.post("/api/queue", json={"video_id": V1}).json() == {"added": True, "count": 1}
    client.post("/api/queue", json={"video_id": V2})
    assert client.post("/api/queue", json={"video_id": V3, "next": True}).json()["count"] == 3
    assert [v["id"] for v in client.get("/api/queue").json()["videos"]] == [V3, V1, V2]
    assert [v["id"] for v in client.put("/api/queue/order", json={"video_ids": [V2, V1]}).json()["videos"]] == [V2, V1, V3]
    assert client.delete(f"/api/queue/{V1}").json() == {"removed": True, "count": 2}
    assert client.post("/api/queue", json={"video_id": "bad"}).status_code == 400
    assert client.delete("/api/queue").json() == {"cleared": 2, "count": 0}
    assert any(r[0] == "queue_add" for r in ctx.conn.execute("SELECT type FROM events"))
