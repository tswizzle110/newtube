"""SponsorBlock reading: privacy of the lookup, parsing, caching, settings.
Response shape follows SponsorBlock's documented API; `selftest --dump` writes real ones
(sponsorblock_prefix.json) to add as fixtures."""
import hashlib
import json

import httpx
import pytest

from newtube.config import Settings
from newtube.net.http import OutboundClient
from newtube.net.ratelimit import RateLimiter
from newtube.sponsorblock.provider import (
    SponsorBlockApi, SponsorBlockError, hash_prefix, parse_response,
)
from newtube.sponsorblock.service import (
    DEFAULT_SETTINGS, SettingsError, SponsorBlockService, load_settings, save_settings, validate_settings,
)
from tests.test_api import FakeExtractor, make_client

VID = "dQw4w9WgXcQ"
OTHER = "jNQXAC9IVRw"


def seg(cat="sponsor", a=10.0, b=20.0, action="skip", uuid="u1", **kw):
    return {"category": cat, "actionType": action, "segment": [a, b], "UUID": uuid,
            "videoDuration": 213, "locked": 0, "votes": 3, "description": "", **kw}


def entry(video_id, *segments):
    return {"videoID": video_id, "hash": hashlib.sha256(video_id.encode()).hexdigest(), "segments": list(segments)}


# ---- privacy of the lookup + parsing ---------------------------------------------------------

def test_lookup_uses_only_a_4_char_hash_prefix():
    p = hash_prefix(VID)
    assert p == hashlib.sha256(VID.encode()).hexdigest()[:4] and len(p) == 4


def test_parse_picks_only_our_video_from_a_shared_prefix_response():
    data = [entry(OTHER, seg(uuid="theirs")), entry(VID, seg(a=30, b=40, uuid="b"), seg(a=5, b=9, uuid="a"))]
    out = parse_response(data, VID)
    assert [s.uuid for s in out.segments] == ["a", "b"]                  # sorted by start, theirs excluded
    s = out.segments[0]
    assert (s.start, s.end, s.category, s.action, s.votes, s.video_duration) == (5, 9, "sponsor", "skip", 3, 213)


def test_full_video_labels_and_highlights():
    data = [entry(VID, seg("sponsor", 0, 0, "full", "f1"), seg("exclusive_access", 0, 0, "full", "f2"),
                  seg("poi_highlight", 66, 66, "poi", "p1"), seg("sponsor", 1, 2, "mute", "m1"))]
    out = parse_response(data, VID)
    assert out.full_labels == ["sponsor", "exclusive_access"]            # labels, never segments to skip
    assert [(s.uuid, s.action, s.start == s.end) for s in out.segments] == [("m1", "mute", False), ("p1", "poi", True)]
    assert all(s.action != "full" for s in out.segments)


@pytest.mark.parametrize("bad", [
    seg(cat="made_up"), seg(action="chapter"), seg(a=20, b=10), seg(a=-1, b=5),
    {"category": "sponsor"}, {"category": "sponsor", "segment": ["x", 2]}, "junk", None])
def test_parse_skips_malformed_segments(bad):
    assert [s.uuid for s in parse_response([entry(VID, bad, seg(uuid="ok"))], VID).segments] == ["ok"]   # good ones survive
    assert [s.uuid for s in parse_response([entry(VID, bad)], VID).segments] == []


def test_parse_survives_garbage_responses():
    for junk in (None, {}, "x", [None, 3, {"videoID": VID}], [{"videoID": VID, "segments": None}]):
        assert parse_response(junk, VID).segments == []


# ---- the API client --------------------------------------------------------------------------

class NoYouTubeLimiter(RateLimiter):
    async def acquire(self, lane=None):                       # the YouTube limiter must stay untouched
        raise AssertionError("SponsorBlock requests must not use the YouTube rate limiter")


def api_with(handler):
    http = OutboundClient(Settings(), NoYouTubeLimiter(0, 0, 10), transport=httpx.MockTransport(handler))
    return SponsorBlockApi(http, RateLimiter(0, 0, 10**6)), http


async def test_request_never_contains_the_video_id_and_asks_for_the_right_things():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json=[entry(VID, seg())])

    api, _ = api_with(handler)
    out = await api.segments(VID)
    assert len(out.segments) == 1
    url = str(seen[0].url)
    assert VID not in url and hash_prefix(VID) in url and url.startswith("https://sponsor.ajay.app/api/skipSegments/")
    q = seen[0].url.params
    assert "sponsor" in json.loads(q["categories"]) and "poi_highlight" in json.loads(q["categories"])
    assert set(json.loads(q["actionTypes"])) == {"skip", "mute", "full", "poi"}


async def test_404_means_no_segments_and_other_failures_raise():
    api, _ = api_with(lambda r: httpx.Response(404))
    assert (await api.segments(VID)).segments == []
    for status in (500, 429, 403):
        api, _ = api_with(lambda r, s=status: httpx.Response(s))
        with pytest.raises(SponsorBlockError):
            await api.segments(VID)
    api, _ = api_with(lambda r: httpx.Response(200, text="<html>not json"))
    with pytest.raises(SponsorBlockError):
        await api.segments(VID)

    def boom(req):
        raise httpx.ConnectError("unreachable")
    api, _ = api_with(boom)
    with pytest.raises(SponsorBlockError):
        await api.segments(VID)


# ---- cache + service -------------------------------------------------------------------------

async def test_service_caches_hits_and_misses_but_not_failures(tmp_path):
    calls, status = [], [200]

    def handler(req):
        calls.append(1)
        if "sponsor.ajay.app" not in str(req.url):
            return httpx.Response(404)
        return httpx.Response(200, json=[entry(VID, seg())]) if status[0] == 200 else httpx.Response(status[0])

    with make_client(tmp_path, handler, FakeExtractor()) as (client, ctx):
        for _ in range(3):
            r = client.get(f"/api/sponsorblock/{VID}").json()
        assert len(r["segments"]) == 1 and r["error"] is None and len(calls) == 1      # cached
        # a failure is reported but not remembered
        status[0] = 500
        r = client.get("/api/sponsorblock/jNQXAC9IVRw").json()
        assert r["segments"] == [] and "HTTP 500" in r["error"]
        status[0] = 200
        assert client.get("/api/sponsorblock/jNQXAC9IVRw").json()["error"] is None


def test_api_validates_the_video_id_and_requires_login(tmp_path):
    from fastapi.testclient import TestClient
    with make_client(tmp_path, lambda r: httpx.Response(404), FakeExtractor()) as (client, ctx):
        assert client.get("/api/sponsorblock/short").status_code == 400
        assert TestClient(client.app).get(f"/api/sponsorblock/{VID}").status_code == 401


# ---- settings --------------------------------------------------------------------------------

def test_defaults_cover_every_category_with_a_valid_action():
    from newtube.sponsorblock.models import ACTIONS, CATEGORIES
    assert set(DEFAULT_SETTINGS["categories"]) == set(CATEGORIES)
    assert all(a in ACTIONS for a in DEFAULT_SETTINGS["categories"].values())
    assert DEFAULT_SETTINGS["contribute"] is False                    # opt-in: it reveals video ids


def test_settings_round_trip_and_partial_updates(tmp_path):
    from newtube.db.connection import connect, migrate
    c = connect(tmp_path / "t.db"); migrate(c)
    assert load_settings(c) == DEFAULT_SETTINGS
    save_settings(c, {"categories": {"sponsor": "button"}, "min_length": 3, "channels": {"UC" + "a" * 22: {"intro": "ignore"}}})
    s = load_settings(c)
    assert s["categories"]["sponsor"] == "button" and s["categories"]["intro"] == "button"   # others keep defaults
    assert s["min_length"] == 3.0 and s["channels"]["UC" + "a" * 22] == {"intro": "ignore"}


@pytest.mark.parametrize("bad", [
    {"nope": 1}, {"categories": {"sponsor": "explode"}}, {"categories": {"made_up": "auto"}}, {"categories": []},
    {"min_length": -1}, {"min_length": 61}, {"min_length": True}, {"min_length": "5"},
    {"contribute": "yes"}, {"show_toast": 1}, {"channels": {"notachannel": {"sponsor": "auto"}}},
    {"channels": {"UC" + "a" * 22: {"sponsor": "boom"}}}, {"channels": {"UC" + "a" * 22: []}}, "string"])
def test_invalid_settings_are_rejected(bad):
    with pytest.raises(SettingsError):
        validate_settings(bad)


def test_empty_channel_override_is_dropped_and_a_corrupt_stored_value_cannot_break_playback(tmp_path):
    from newtube.db.connection import connect, migrate
    from newtube.db.queries import settings as kv
    c = connect(tmp_path / "t.db"); migrate(c)
    assert save_settings(c, {"channels": {"UC" + "a" * 22: {}}})["channels"] == {}
    kv.put(c, "sponsorblock", {"categories": {"sponsor": "garbage"}})
    assert load_settings(c) == DEFAULT_SETTINGS


def test_settings_api(tmp_path):
    with make_client(tmp_path, lambda r: httpx.Response(404), FakeExtractor()) as (client, ctx):
        s = client.get("/api/settings/sponsorblock").json()
        assert s["contribute"] is False and s["category_order"][0] == "sponsor" and "sponsor" in s["category_labels"]
        r = client.put("/api/settings/sponsorblock", json={**s, "min_length": 2, "categories": {**s["categories"], "intro": "ignore"}})
        assert r.status_code == 200 and r.json()["categories"]["intro"] == "ignore"
        assert client.get("/api/settings/sponsorblock").json()["min_length"] == 2
        assert client.put("/api/settings/sponsorblock", json={"min_length": 999}).status_code == 400
