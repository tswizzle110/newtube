import httpx
import pytest
from fastapi.testclient import TestClient

from newtube.app import Context, create_app
from newtube.auth import Auth
from newtube.config import Settings
from newtube.db.connection import connect, migrate
from newtube.extract.base import ChannelInfo, Chapter, Format, VideoInfo, VideoUnavailable
from newtube.media.diskcache import DiskCache
from newtube.net.http import OutboundClient
from newtube.net.ratelimit import RateLimiter
from newtube.services.videos import VideoService
from tests.fixtures_rss import SAMPLE_FEED

VID = "aaaaaaaaaaa"
CID = "UCBR8-60-B28hp2BmDPdntcQ"


class _Body(httpx.AsyncByteStream):
    """A response body that is really streamed (MockTransport pre-reads `content=`)."""

    def __init__(self, data: bytes):
        self.data = data

    async def __aiter__(self):
        yield self.data


class FakeExtractor:
    """Hands out a new signed URL on every extraction, like YouTube does."""

    def __init__(self):
        self.calls = 0

    async def video_info(self, video_id, *, background=False):
        if video_id == "unavailable":
            raise VideoUnavailable("Video unavailable")
        self.calls += 1
        return VideoInfo(
            video_id, "Title", CID, "Chan", "desc", 100, 1700000000, 5, "Music", ["t"], None,
            False, 0, chapters=[Chapter(0, "Intro")],
            formats=[Format("18", f"https://rr1.googlevideo.com/vp?sig=v{self.calls}", "mp4",
                            "avc1", "mp4a", height=360)],
            fetched_at=1)

    async def resolve_channel(self, ref, *, background=False):
        return ChannelInfo(CID, "YouTube", "@YouTube", "https://yt3.ggpht.com/av")


import contextlib


@contextlib.contextmanager
def make_client(tmp_path, handler, ex):
    """A fully wired app (mock network, fake extractor, logged-in client)."""
    s = Settings(data_dir=tmp_path)
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    lim = RateLimiter(0, 0, 100)
    auth = Auth(tmp_path / "auth.json")
    auth.set_password("correct horse")
    ctx = Context(s, conn, lim, OutboundClient(s, lim, transport=httpx.MockTransport(handler)),
                  ex, VideoService(conn, ex), DiskCache(tmp_path / "img", 10**6), auth,
                  run_scheduler=False)
    with TestClient(create_app(ctx)) as client:
        assert client.post("/api/login", json={"password": "correct horse"}).status_code == 200
        yield client, ctx


@pytest.fixture
def env(tmp_path):
    seen = []   # every upstream request, for assertions

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        u = str(req.url)
        if "i.ytimg.com" in u:
            return httpx.Response(200, content=b"JPEG", headers={"content-type": "image/jpeg"})
        if "yt3.ggpht.com" in u:
            return httpx.Response(200, content=b"AVATAR", headers={"content-type": "image/png"})
        if "videos.xml" in u:
            return httpx.Response(200, text=SAMPLE_FEED)
        if "googlevideo.com" in u:
            if "sig=v1" in u:      # first signed URL has "expired"
                return httpx.Response(403)
            return httpx.Response(206, stream=_Body(b"v" * 10), headers={
                "content-type": "video/mp4", "content-range": "bytes 0-9/100",
                "content-length": "10", "x-secret": "no"})
        return httpx.Response(404)

    ex = FakeExtractor()
    with make_client(tmp_path, handler, ex) as (client, ctx):
        yield client, ctx, ex, seen


def test_thumbnail_is_fetched_once_then_cached(env):
    client, ctx, ex, seen = env
    for _ in range(3):
        r = client.get(f"/api/thumb/{VID}/hqdefault")
        assert r.status_code == 200 and r.content == b"JPEG"
    assert sum("ytimg" in str(q.url) for q in seen) == 1


def test_thumbnail_validates_input(env):
    client, *_ = env
    assert client.get("/api/thumb/short/hqdefault").status_code == 400
    assert client.get(f"/api/thumb/{VID}/../../etc").status_code in (400, 404)
    assert client.get(f"/api/thumb/{VID}/evil").status_code == 400


def test_video_json_hides_stream_urls_and_persists(env):
    client, ctx, *_ = env
    r = client.get(f"/api/video/{VID}")
    assert r.status_code == 200
    assert "googlevideo" not in r.text and "sig=" not in r.text
    body = r.json()
    assert body["formats"][0]["format_id"] == "18" and body["chapters"] == [{"start": 0, "title": "Intro"}]
    row = ctx.conn.execute("SELECT title, detail_fetched_at FROM videos WHERE id=?", (VID,)).fetchone()
    assert row["title"] == "Title" and row["detail_fetched_at"] == 1
    assert ctx.conn.execute("SELECT count(*) FROM chapters").fetchone()[0] == 1


def test_video_unavailable_maps_to_404(env):
    client, *_ = env
    assert client.get("/api/video/unavailable").status_code == 404


def test_stream_clamps_range_and_retries_with_fresh_url_on_403(env):
    client, ctx, ex, seen = env
    r = client.get(f"/api/stream/{VID}/18", headers={"Range": "bytes=0-"})
    assert r.status_code == 206 and r.content == b"v" * 10
    assert r.headers["content-range"] == "bytes 0-9/100"
    assert "x-secret" not in r.headers                  # only whitelisted headers pass through
    gv = [q for q in seen if "googlevideo" in str(q.url)]
    assert len(gv) == 2 and ex.calls == 2               # 403 -> re-extracted -> retried
    assert all(q.headers["range"] == "bytes=0-10485759" for q in gv)


def test_stream_unknown_format_404(env):
    client, *_ = env
    assert client.get(f"/api/stream/{VID}/999").status_code == 404


def test_subscribe_feed_and_unsubscribe_flow(env):
    client, ctx, *_ = env
    r = client.post("/api/subscriptions", json={"ref": "@YouTube"})
    assert r.status_code == 200 and r.json()["created"] and r.json()["new_videos"] == 2
    assert client.post("/api/subscriptions", json={"ref": "@YouTube"}).json()["created"] is False
    feed = client.get("/api/feed").json()
    assert [v["id"] for v in feed] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]
    assert client.get(f"/api/avatar/{CID}").content == b"AVATAR"
    assert client.get("/api/subscriptions").json()[0]["handle"] == "@YouTube"
    assert client.delete(f"/api/subscriptions/{CID}").json() == {"removed": True}
    assert client.get("/api/feed").json() == []


def test_subscribe_reports_feed_failure_instead_of_hiding_it(tmp_path):
    def handler(req):
        return httpx.Response(500) if "videos.xml" in str(req.url) else httpx.Response(404)

    with make_client(tmp_path, handler, FakeExtractor()) as (client, ctx):
        r = client.post("/api/subscriptions", json={"ref": "@YouTube"}).json()
        assert r["created"] is True and r["new_videos"] == 0
        assert "HTTP 500" in r["poll_error"]


def test_cache_entries_from_older_code_are_ignored(env):
    """Regression: video info cached before a format change (e.g. before language_preference
    existed) must not be served; it made the player choose the wrong audio track."""
    import json, time
    client, ctx, ex, _ = env
    stale = {"id": "aaaaaaaaaaa", "title": "STALE", "formats": []}
    ctx.conn.execute("INSERT INTO meta_cache(key,payload,fetched_at,expires_at) VALUES (?,?,?,?)",
                     ("video:aaaaaaaaaaa", json.dumps(stale), 1, int(time.time()) + 9999))
    assert client.get("/api/video/aaaaaaaaaaa").json()["title"] == "Title"
    assert ex.calls == 1


def test_avatar_stored_in_old_protocol_relative_form_still_loads(env):
    """Regression: avatars saved as '//yt3.ggpht.com/..' (as YouTube returns them) were refused
    by the https-only allowlist, giving a broken image for every subscription."""
    client, ctx, *_ = env
    ctx.conn.execute("INSERT INTO channels(id,title,avatar_url,created_at) VALUES (?,?,?,1)",
                     (CID, "Chan", "//yt3.ggpht.com/av=s88"))
    assert client.get(f"/api/avatar/{CID}").content == b"AVATAR"
