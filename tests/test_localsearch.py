"""Search that stays on this server: suggestions from your own searches, and a search inside a channel's saved videos."""
import json

import pytest

from newtube.db.connection import connect, migrate
from newtube.db.queries import localsearch as ls
from newtube.db.queries.events import log_event
from tests.test_api import env  # noqa: F401  (a fixture)

CH, OTHER = "UC" + "a" * 22, "UC" + "b" * 22


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    for cid, name in ((CH, "Alpha"), (OTHER, "Other")):
        c.execute("INSERT INTO channels(id,title,created_at) VALUES (?,?,1)", (cid, name))
    return c


def searched(conn, *queries):
    for q in queries:
        log_event(conn, "search", payload={"q": q, "kind": "video", "n": 5})


def test_suggestions_are_your_own_past_searches_newest_first_without_repeats(conn):
    searched(conn, "sourdough starter", "retro computing", "Sourdough Starter", "python asyncio")
    assert ls.suggestions(conn) == ["python asyncio", "Sourdough Starter", "retro computing"]


def test_typing_narrows_them_starting_with_before_containing(conn):
    searched(conn, "bread baking", "sourdough bread", "bread machine", "tax")
    assert ls.suggestions(conn, "bre") == ["bread machine", "bread baking", "sourdough bread"]      # (starting with first, then containing)
    assert ls.suggestions(conn, "bread") == ["bread machine", "bread baking", "sourdough bread"]
    assert ls.suggestions(conn, "BREAD MACHINE") == []                       # exactly what is typed is not "suggested"
    assert ls.suggestions(conn, "zzz") == []


def test_damaged_and_overlong_entries_are_skipped_and_the_list_is_capped(conn):
    conn.execute("INSERT INTO events(ts,type,payload) VALUES (1,'search','not json')")
    conn.execute("INSERT INTO events(ts,type,payload) VALUES (1,'search',?)", (json.dumps({"q": "x" * 500}),))
    searched(conn, *[f"query {i}" for i in range(20)])
    got = ls.suggestions(conn)
    assert len(got) == 8 and got[0] == "query 19" and all(len(q) < 200 for q in got)


def add_video(conn, vid, title, channel=CH, published=100):
    conn.execute("INSERT INTO videos(id,channel_id,title,published_at,first_seen_at) VALUES (?,?,?,?,1)", (vid, channel, title, published))


def test_a_channel_search_looks_in_its_saved_uploads_and_stored_videos(conn):
    conn.execute("INSERT INTO channel_uploads(channel_id,video_id,pos,title,duration,view_count) VALUES (?,?,?,?,?,?)", (CH, "up000000001", 0, "Sourdough part 2", 600, 9))
    conn.execute("INSERT INTO channel_uploads(channel_id,video_id,pos,title,duration,view_count) VALUES (?,?,?,?,?,?)", (CH, "up000000002", 1, "Gardening day", 600, 9))
    add_video(conn, "st000000001", "More SOURDOUGH tips")                  # a stored video that is not in the upload list
    add_video(conn, "ot000000001", "Sourdough elsewhere", channel=OTHER)   # another channel: never
    found, n = ls.channel_videos(conn, CH, "sourdough")
    assert [v["id"] for v in found] == ["up000000001", "st000000001"] and n == 3
    assert found[0]["channel_title"] == "Alpha" and found[0]["duration"] == 600


def test_an_empty_query_searches_nothing_and_a_miss_finds_nothing(conn):
    add_video(conn, "st000000001", "Hello")
    assert ls.channel_videos(conn, CH, "  ") == ([], 1)
    assert ls.channel_videos(conn, CH, "nothing like it")[0] == []


def test_the_apis(env):
    client, ctx, ex, seen = env
    searched(ctx.conn, "sourdough bread")
    ctx.conn.execute("INSERT OR IGNORE INTO channels(id,title,created_at) VALUES (?,?,1)", (CH, "Alpha"))
    add_video(ctx.conn, "st000000001", "Sourdough basics")
    assert client.get("/api/search/suggest?q=sour").json() == ["sourdough bread"]
    assert client.get("/api/search/suggest").json() == ["sourdough bread"]
    r = client.get(f"/api/channel/{CH}/search?q=sourdough").json()
    assert [v["id"] for v in r["videos"]] == ["st000000001"] and r["searched"] == 1
    assert client.get("/api/channel/bad/search?q=x").status_code == 400
    assert client.get(f"/api/channel/{CH}/search?q=").status_code == 422
