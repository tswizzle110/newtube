"""The server must keep answering while something slow is worked out: a video that is starting must not wait for the
recommender or for Up next. (To you, a blocked server looks like a video that will not start: "connection problem".)"""
import asyncio
import threading
import time

import pytest

from newtube.db.connection import connect, migrate
from newtube.loopwatch import LoopWatch
from newtube.ml import store, taste
from newtube.ml.embedder import HashingEmbedder
from newtube.services.related import RelatedService
from tests.test_api import env  # noqa: F401  (a fixture)

NOW = 1_800_000_000


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    return c


def add(conn, vid, title, channel="UC" + "a" * 22):
    conn.execute("INSERT OR IGNORE INTO channels(id,title,created_at) VALUES (?,?,1)", (channel, "Chan"))
    conn.execute("INSERT INTO videos(id,channel_id,title,first_seen_at) VALUES (?,?,?,1)", (vid, channel, title))


# ---- the watchdog -------------------------------------------------------------------------------------

def test_the_watchdog_names_what_blocked_the_loop():
    def a_slow_function_on_the_loop():
        time.sleep(0.9)

    async def main():
        w = LoopWatch(stall=0.4, beat=0.05)
        task = asyncio.create_task(w.run())
        await asyncio.sleep(0.2)
        a_slow_function_on_the_loop()                         # (what a heavy computation on the loop looks like)
        await asyncio.sleep(0.2)
        task.cancel()
        return w.reports

    reports = asyncio.run(main())
    assert len(reports) == 1                                  # one report per blocking episode, not one per beat
    assert "blocked" in reports[0] and "a_slow_function_on_the_loop" in reports[0]


def test_the_watchdog_is_silent_when_the_loop_is_free():
    async def main():
        w = LoopWatch(stall=0.3, beat=0.05)
        task = asyncio.create_task(w.run())
        for _ in range(8):
            await asyncio.sleep(0.05)
        task.cancel()
        return w.reports

    assert asyncio.run(main()) == []


# ---- heavy work is bounded or cached ------------------------------------------------------------------

def test_the_fallback_compares_a_few_hundred_videos_not_the_whole_library(conn, monkeypatch):
    add(conn, "aaaaaaaaaaa", "python asyncio tutorial for beginners")
    for i in range(30):
        add(conn, f"b{i:010d}", f"unrelated gardening video number {i}")
    add(conn, "cccccccccc1", "python asyncio advanced patterns")
    store.embed_pending(conn, HashingEmbedder(), limit=100)
    asked = []
    real = store.load_vectors
    monkeypatch.setattr(store, "load_vectors", lambda c, model, ids=None: (asked.append(ids), real(c, model, ids))[1])
    got = [c["id"] for c in RelatedService(conn, None).local_cards("aaaaaaaaaaa")]
    assert "cccccccccc1" in got                               # the similar title is still found
    assert asked and all(ids is not None for ids in asked)    # ...and a vector for the whole library was never asked for
    assert all(len(ids) < 10 for ids in asked)                # only the few that share a defining word


def test_closest_to_your_taste_is_reused_whatever_events_come_in(conn, monkeypatch):
    for i in range(4):
        add(conn, f"w{i:010d}", f"funny cats playing piano {i}")
        conn.execute("INSERT INTO watch_sessions(id,video_id,started_at,intervals,max_position,duration) VALUES (?,?,?,?,60,100)",
                     (f"s{i}", f"w{i:010d}", NOW - 100, "[[0,60]]"))
    store.embed_pending(conn, HashingEmbedder(), limit=100)
    loads = []
    real = taste.load_vectors
    monkeypatch.setattr(taste, "load_vectors", lambda c, model, ids=None: (loads.append(ids), real(c, model, ids))[1])
    first = taste.scorer(conn, HashingEmbedder().name, NOW).nearest(3)
    full_loads = lambda: sum(1 for ids in loads if ids is None)                  # noqa: E731
    assert full_loads() == 1
    conn.execute("INSERT INTO events(ts,type,video_id) VALUES (?,?,?)", (NOW, "play", "w0000000000"))   # every page view logs events
    assert taste.scorer(conn, HashingEmbedder().name, NOW).nearest(3) == first
    assert full_loads() == 1                                                      # not worked out again


def test_taste_is_still_recomputed_after_ten_minutes(conn, monkeypatch):
    add(conn, "w0000000000", "funny cats playing piano")
    conn.execute("INSERT INTO watch_sessions(id,video_id,started_at,intervals,max_position,duration) VALUES ('s','w0000000000',?,'[[0,60]]',60,100)", (NOW,))
    store.embed_pending(conn, HashingEmbedder(), limit=100)
    taste.scorer(conn, HashingEmbedder().name, NOW).nearest(3)
    key = (id(conn), HashingEmbedder().name)
    until, k, ids = taste._near[key]
    taste._near[key] = (time.monotonic() - 1, k, ids)                              # expired
    add(conn, "n0000000000", "more funny cats playing piano")
    store.embed_pending(conn, HashingEmbedder(), limit=100)
    assert "n0000000000" in taste.scorer(conn, HashingEmbedder().name, NOW).nearest(10)


# ---- the slow parts run off the main thread -----------------------------------------------------------

def test_recommendations_and_the_up_next_fallback_run_on_worker_threads(env, monkeypatch):
    client, ctx, ex, seen = env
    main = threading.get_ident()
    ran = {}
    from newtube.api import recommendations as rec_api

    monkeypatch.setattr(rec_api, "compute", lambda c, limit: ran.setdefault("recommendations", threading.get_ident()) and [])
    assert client.get("/api/recommendations").status_code == 200

    def fake_local(video_id, limit):
        ran["fallback"] = threading.get_ident()
        return []

    ctx.related.local_cards = fake_local
    ctx.related.extractor = type("Ex", (), {"related": staticmethod(lambda *a, **k: asyncio.sleep(0, [])), "search": staticmethod(lambda *a, **k: asyncio.sleep(0, []))})()
    assert client.get("/api/related/aaaaaaaaaaa").status_code == 200
    assert ran.get("recommendations") not in (None, main) and ran.get("fallback") not in (None, main), ran
