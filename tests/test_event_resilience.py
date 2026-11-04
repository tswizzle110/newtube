"""The browser keeps what it could not send (laptop asleep, wifi switched) and sends it again: the server must take
each batch exactly once, and file late reports under the time they actually happened."""
import json
import time

from newtube.api.events import MAX_BACKDATE, when
from tests.test_api import VID, env  # noqa: F401


def post(client, body):
    r = client.post("/api/events", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def clicks(ctx):
    return ctx.conn.execute("SELECT ts FROM events WHERE type='click' ORDER BY id").fetchall()


# ---- the same batch twice -----------------------------------------------------------------------------

def test_a_batch_sent_twice_is_recorded_once(env):
    client, ctx, ex, seen = env
    batch = {"batch_id": "b1", "clicks": [{"video_id": VID, "surface": "subs"}],
             "events": [{"type": "like", "video_id": VID}],
             "watch": [{"session_id": "s1", "video_id": VID, "position": 5, "duration": 100, "intervals": [[0, 5]]}]}
    first, second = post(client, batch), post(client, batch)
    assert first["duplicate"] == 0 and first["clicks"] == 1 and first["events"] == 1 and first["watch"] == 1
    assert second["duplicate"] == 1 and second["clicks"] == 0 and second["events"] == 0 and second["watch"] == 0
    assert len(clicks(ctx)) == 1
    assert ctx.conn.execute("SELECT count(*) FROM events WHERE type='like'").fetchone()[0] == 1
    assert ctx.conn.execute("SELECT count(*) FROM watch_sessions").fetchone()[0] == 1


def test_different_batches_are_both_taken_and_batches_without_an_id_still_work(env):
    client, ctx, ex, seen = env
    post(client, {"batch_id": "a", "clicks": [{"video_id": VID, "surface": "subs"}]})
    post(client, {"batch_id": "b", "clicks": [{"video_id": VID, "surface": "subs"}]})
    post(client, {"clicks": [{"video_id": VID, "surface": "subs"}]})
    post(client, {"clicks": [{"video_id": VID, "surface": "subs"}]})            # (no id: nothing to recognise it by)
    assert len(clicks(ctx)) == 4


def test_a_rejected_batch_leaves_no_trace_so_a_corrected_resend_is_not_called_a_duplicate(env):
    client, ctx, ex, seen = env
    assert client.post("/api/events", json={"batch_id": "bad", "clicks": [{"surface": "subs"}]}).status_code == 422
    assert ctx.conn.execute("SELECT count(*) FROM event_batches WHERE id='bad'").fetchone()[0] == 0
    assert post(client, {"batch_id": "bad", "clicks": [{"video_id": VID, "surface": "subs"}]})["clicks"] == 1


# ---- late reports keep their real time ----------------------------------------------------------------

def test_when_corrects_for_a_wrong_laptop_clock_and_never_invents_the_future_or_ancient_history():
    now = 1_800_000_000
    assert when(None, 0, now) == now                                    # unknown: now
    assert when(now - 600, 0, now) == now - 600                         # clocks agree: as reported
    assert when(now - 600 - 3600, 3600, now) == now - 600               # the laptop is an hour behind: corrected
    assert when(now + 5000, 0, now) == now                              # not in the future
    assert when(now - 10 * MAX_BACKDATE, 0, now) == now - MAX_BACKDATE  # not absurdly old


def test_a_delayed_batch_is_filed_under_when_it_happened(env):
    client, ctx, ex, seen = env
    now = time.time()
    # This batch is being sent now, but the click happened 6 hours ago (laptop was closed); the laptop clock is 2 min fast.
    post(client, {"batch_id": "late", "sent_at": now + 120, "clicks": [{"video_id": VID, "surface": "subs", "ts": now + 120 - 6 * 3600}],
                  "events": [{"type": "like", "video_id": VID, "ts": now + 120 - 3600}],
                  "watch": [{"session_id": "late-s", "video_id": VID, "position": 30, "duration": 100, "intervals": [[0, 30]],
                             "started_at": now + 120 - 7200}]})
    (click,) = clicks(ctx)
    assert abs(click["ts"] - (now - 6 * 3600)) <= 3
    like = ctx.conn.execute("SELECT ts FROM events WHERE type='like'").fetchone()
    assert abs(like["ts"] - (now - 3600)) <= 3
    sess = ctx.conn.execute("SELECT started_at FROM watch_sessions WHERE id='late-s'").fetchone()
    assert abs(sess["started_at"] - (now - 7200)) <= 3


def test_a_later_heartbeat_does_not_move_a_sessions_start(env):
    client, ctx, ex, seen = env
    now = time.time()
    w = {"session_id": "s", "video_id": VID, "position": 10, "duration": 100, "intervals": [[0, 10]]}
    post(client, {"watch": [{**w, "started_at": now - 1000}]})
    post(client, {"watch": [{**w, "position": 20, "intervals": [[0, 20]], "started_at": now - 5}]})
    started = ctx.conn.execute("SELECT started_at FROM watch_sessions WHERE id='s'").fetchone()[0]
    assert abs(started - (now - 1000)) <= 3


def test_old_batch_ids_are_purged_but_recent_ones_are_kept(env):
    from newtube.config import Settings
    from newtube.jobs.scheduler import Scheduler
    client, ctx, ex, seen = env
    ctx.conn.execute("INSERT INTO event_batches VALUES ('old', ?)", (int(time.time()) - 15 * 86400,))
    ctx.conn.execute("INSERT INTO event_batches VALUES ('new', ?)", (int(time.time()) - 86400,))
    Scheduler(Settings(), ctx.conn, http=None, extractor=None, clock=lambda: 10**6).housekeeping()
    assert [r[0] for r in ctx.conn.execute("SELECT id FROM event_batches")] == ["new"]
