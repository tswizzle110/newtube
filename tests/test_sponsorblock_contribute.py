"""Submitting and voting: opt-in, anonymity, validation, error handling."""
import json

import httpx
import pytest

from tests.test_api import FakeExtractor, make_client

VID = "aaaaaaaaaaa"          # FakeExtractor reports a 100 s duration for every video
sb_calls = []


def handler_factory(post_status=200, post_text="OK", boom=False):
    sb_calls.clear()

    def handler(req: httpx.Request) -> httpx.Response:
        if "sponsor.ajay.app" not in str(req.url):
            return httpx.Response(404)
        body = json.loads(req.content) if req.content else None
        sb_calls.append((req.method, req.url.path, body))
        if req.method == "POST":
            if boom:
                raise httpx.ConnectError("down")
            return httpx.Response(post_status, text=post_text)
        return httpx.Response(404)
    return handler


def enable(client, on=True):
    s = client.get("/api/settings/sponsorblock").json()
    s["contribute"] = on
    assert client.put("/api/settings/sponsorblock", json=s).status_code == 200


def submit(client, **kw):
    body = {"start": 12.3456, "end": 20, "category": "sponsor", "action": "skip", **kw}
    return client.post(f"/api/sponsorblock/{VID}/submit", json=body)


def posts():
    return [c for c in sb_calls if c[0] == "POST"]


def test_everything_is_refused_while_contributing_is_off_and_nothing_is_sent(tmp_path):
    with make_client(tmp_path, handler_factory(), FakeExtractor()) as (client, ctx):
        r = submit(client)
        assert r.status_code == 403 and "turned off" in r.json()["detail"]
        v = client.post("/api/sponsorblock/vote", json={"uuid": "abcdefgh12345678", "type": "up"})
        assert v.status_code == 403
        assert posts() == []


def test_submission_body_is_exactly_what_sponsorblock_expects(tmp_path):
    with make_client(tmp_path, handler_factory(), FakeExtractor()) as (client, ctx):
        enable(client)
        assert submit(client).json() == {"ok": True}
        (_, path, body), = posts()
        assert path == "/api/skipSegments"
        assert body["videoID"] == VID and body["videoDuration"] == 100.0 and body["userAgent"] == "newtube/0.1"
        assert body["segments"] == [{"segment": [12.346, 20.0], "category": "sponsor", "actionType": "skip"}]
        assert len(body["userID"]) == 64 and all(c in "0123456789abcdef" for c in body["userID"])


def test_the_anonymous_id_is_stable_stays_on_the_server_and_is_not_an_account(tmp_path):
    with make_client(tmp_path, handler_factory(), FakeExtractor()) as (client, ctx):
        enable(client)
        r1 = submit(client)
        r2 = client.post("/api/sponsorblock/vote", json={"uuid": "abcdefgh12345678", "type": "up"})
        ids = {c[2]["userID"] for c in posts()}
        assert len(ids) == 1                                          # same random id every time
        uid = ids.pop()
        leaked = [r1.text, r2.text, client.get("/api/settings/sponsorblock").text, client.get(f"/api/sponsorblock/{VID}").text]
        assert not any(uid in t for t in leaked)                      # never sent to the browser
        assert VID not in uid and uid != "" and ctx.sb_contrib.user_id() == uid


@pytest.mark.parametrize("override", [
    {"end": 12.3456}, {"end": 12.5}, {"end": 12}, {"end": 150}, {"start": 500, "end": 510},
    {"category": "made_up"}, {"action": "chapter"}, {"action": "poi"}, {"category": "poi_highlight"},
])
def test_invalid_submissions_are_rejected_before_anything_is_sent(tmp_path, override):
    with make_client(tmp_path, handler_factory(), FakeExtractor()) as (client, ctx):
        enable(client)
        assert submit(client, **override).status_code == 400
        assert posts() == []


def test_out_of_range_numbers_are_refused_by_validation(tmp_path):
    with make_client(tmp_path, handler_factory(), FakeExtractor()) as (client, ctx):
        enable(client)
        assert submit(client, start=-1).status_code == 422
        assert posts() == []


def test_a_highlight_is_a_single_point(tmp_path):
    with make_client(tmp_path, handler_factory(), FakeExtractor()) as (client, ctx):
        enable(client)
        assert submit(client, category="poi_highlight", action="poi", start=33, end=60).status_code == 200
        assert posts()[0][2]["segments"] == [{"segment": [33.0, 33.0], "category": "poi_highlight", "actionType": "poi"}]


@pytest.mark.parametrize("status,text,expect_status,expect_words", [
    (409, "", 409, "already submitted"), (429, "", 429, "rate limiting"), (400, "Bad segment", 400, "Bad segment"),
    (403, "banned", 403, "banned"), (500, "", 502, "HTTP 500")])
def test_sponsorblock_errors_become_readable_messages(tmp_path, status, text, expect_status, expect_words):
    with make_client(tmp_path, handler_factory(status, text), FakeExtractor()) as (client, ctx):
        enable(client)
        r = submit(client)
        assert r.status_code == expect_status and expect_words in r.json()["detail"]


def test_network_failure_is_reported_not_crashed(tmp_path):
    with make_client(tmp_path, handler_factory(boom=True), FakeExtractor()) as (client, ctx):
        enable(client)
        r = submit(client)
        assert r.status_code == 502 and "Could not reach SponsorBlock" in r.json()["detail"]


def test_submitting_invalidates_the_cached_segments_and_is_logged_locally(tmp_path):
    with make_client(tmp_path, handler_factory(), FakeExtractor()) as (client, ctx):
        enable(client)
        client.get(f"/api/sponsorblock/{VID}"); client.get(f"/api/sponsorblock/{VID}")
        assert len([c for c in sb_calls if c[0] == "GET"]) == 1          # cached
        submit(client)
        client.get(f"/api/sponsorblock/{VID}")
        assert len([c for c in sb_calls if c[0] == "GET"]) == 2          # refetched so the new segment can show
        ev = ctx.conn.execute("SELECT video_id, payload FROM events WHERE type='sb_submit'").fetchone()
        assert ev["video_id"] == VID and '"category": "sponsor"' in ev["payload"]


def test_votes_map_to_sponsorblocks_types_and_are_validated(tmp_path):
    with make_client(tmp_path, handler_factory(), FakeExtractor()) as (client, ctx):
        enable(client)
        for kind, number in (("up", 1), ("down", 0), ("undo", 20)):
            assert client.post("/api/sponsorblock/vote", json={"uuid": "abcdefgh12345678", "type": kind, "video_id": VID}).status_code == 200
            assert posts()[-1][1:] == ("/api/voteOnSponsorTime", {"UUID": "abcdefgh12345678", "userID": ctx.sb_contrib.user_id(), "type": number})
        n = len(posts())
        for bad in ({"uuid": "abcdefgh12345678", "type": "sideways"}, {"uuid": "short", "type": "up"}, {"uuid": "has spaces here!", "type": "up"}):
            assert client.post("/api/sponsorblock/vote", json=bad).status_code == 400
        assert len(posts()) == n
        assert ctx.conn.execute("SELECT count(*) FROM events WHERE type='sb_vote'").fetchone()[0] == 3


def test_contributing_never_touches_the_youtube_rate_limiter(tmp_path):
    with make_client(tmp_path, handler_factory(), FakeExtractor()) as (client, ctx):
        enable(client)

        async def boom(*a, **k):
            raise AssertionError("YouTube limiter used for SponsorBlock")
        ctx.http.limiter.acquire = boom
        assert client.post("/api/sponsorblock/vote", json={"uuid": "abcdefgh12345678", "type": "up"}).status_code == 200


def test_contributing_requires_login(tmp_path):
    from fastapi.testclient import TestClient
    with make_client(tmp_path, handler_factory(), FakeExtractor()) as (client, ctx):
        anon = TestClient(client.app)
        assert anon.post(f"/api/sponsorblock/{VID}/submit", json={"start": 1, "end": 5, "category": "sponsor"}).status_code == 401
        assert anon.post("/api/sponsorblock/vote", json={"uuid": "abcdefgh12345678", "type": "up"}).status_code == 401
