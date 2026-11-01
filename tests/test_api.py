import httpx
import pytest
from fastapi.testclient import TestClient

from newtube.app import Context, create_app
from newtube.auth import Auth
from newtube.config import Settings
from newtube.db.connection import connect, migrate
from newtube.extract.base import (
    ChannelInfo, ChannelSummary, Chapter, Format, PlaylistSummary, VideoInfo, VideoSummary, VideoUnavailable,
)
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
        self.list_calls = 0

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

    # ---- lists (shapes copied from real yt-dlp results; see tests/fixtures_entries.py)
    async def search(self, query, kind="video", limit=20, *, background=False):
        self.list_calls += 1
        return {
            "video": [VideoSummary(VID, "Found video", CID, "Chan", 100, 5)],
            "channel": [ChannelSummary(CID, "YouTube", "@YouTube", "official", "https://yt3.ggpht.com/av", 46600000)],
            "playlist": [PlaylistSummary("PLBDA2E52FB1EF80C9", "World History", "CrashCourse", None,
                                         "https://i.ytimg.com/vi/Yocja_N5s1I/hq720.jpg")],
        }[kind]

    async def channel_page(self, channel_id, limit=30, *, background=False):
        self.list_calls += 1
        # like the real channel tab: entries carry no views / date / channel name
        return (ChannelInfo(channel_id, "YouTube", "@YouTube", "https://yt3.ggpht.com/av", "official", 46600000),
                [VideoSummary(VID, "Upload one", channel_id, "YouTube", 607), VideoSummary("bbbbbbbbbbb", "Upload two", channel_id, "YouTube", 60)])

    async def playlist_page(self, playlist_id, limit=100, *, background=False):
        self.list_calls += 1
        return (PlaylistSummary(playlist_id, "World History", "CrashCourse", 2),
                [VideoSummary(VID, "Part 1", CID, "CrashCourse", 671, 17000000),
                 VideoSummary("bbbbbbbbbbb", "Part 2", CID, "CrashCourse", 575, 9100000)])


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


# ---- search, channel and playlist pages, image proxy --------------------------------------


def test_search_returns_each_kind_and_logs_the_query(env):
    client, ctx, *_ = env
    for kind, key in (("video", "Found video"), ("channel", "YouTube"), ("playlist", "World History")):
        r = client.get("/api/search", params={"q": "zoo", "kind": kind}).json()
        assert r["kind"] == kind and r["results"][0]["kind"] == kind and r["results"][0]["title"] == key
    rows = ctx.conn.execute("SELECT payload FROM events WHERE type='search'").fetchall()
    assert len(rows) == 3 and '"q": "zoo"' in rows[0]["payload"]


def test_search_is_cached_and_validated(env):
    client, ctx, ex, _ = env
    for _ in range(3):
        client.get("/api/search", params={"q": "Same Query", "kind": "video"})
    client.get("/api/search", params={"q": "same query ", "kind": "video"})     # case/space-insensitive
    assert ex.list_calls == 1
    assert client.get("/api/search", params={"q": "x", "kind": "everything"}).status_code == 400
    assert client.get("/api/search", params={"q": ""}).status_code == 422
    assert client.get("/api/search", params={"q": "x" * 201}).status_code == 422
    assert client.get("/api/search", params={"q": "x", "limit": 500}).status_code == 422


def test_search_results_are_enriched_with_local_state_after_the_cache(env):
    client, ctx, *_ = env
    ctx.conn.execute("INSERT INTO channels(id,title,created_at) VALUES (?,?,1)", (CID, "Chan"))
    ctx.conn.execute("INSERT INTO videos(id,channel_id,title,published_at,view_count,first_seen_at)"
                     " VALUES (?,?,?,?,?,1)", (VID, CID, "t", 1700000000, 999))
    ctx.conn.execute("INSERT INTO video_progress VALUES (?,?,?,?,?)", (VID, 40, 100, 0, 1))
    v = client.get("/api/search", params={"q": "zoo"}).json()["results"][0]
    assert (v["published_at"], v["resume_position"], v["completed"]) == (1700000000, 40, False)
    assert v["view_count"] == 5                          # extractor's own value is not overwritten
    ctx.conn.execute("UPDATE video_progress SET position=90, completed=1")
    v2 = client.get("/api/search", params={"q": "zoo"}).json()["results"][0]   # served from cache
    assert (v2["resume_position"], v2["completed"]) == (90, True)               # ...but state is fresh


def test_channel_page_fills_in_the_channel_and_knows_if_subscribed(env):
    client, ctx, ex, _ = env
    page = client.get(f"/api/channel/{CID}").json()
    assert page["channel"]["title"] == "YouTube" and page["channel"]["subscriber_count"] == 46600000
    assert [v["title"] for v in page["videos"]] == ["Upload one", "Upload two"]
    assert page["videos"][0]["channel_title"] == "YouTube" and page["subscribed"] is False
    client.post("/api/subscriptions", json={"ref": "@YouTube"})
    assert client.get(f"/api/channel/{CID}").json()["subscribed"] is True
    client.get(f"/api/channel/{CID}")
    assert ex.list_calls == 1                                     # page cached
    assert client.get("/api/channel/nope").status_code == 400


def test_channel_page_gets_upload_dates_from_rss_data_already_stored(env):
    client, ctx, *_ = env
    client.post("/api/subscriptions", json={"ref": "@YouTube"})  # RSS poll stores 'aaaaaaaaaaa'
    first = client.get(f"/api/channel/{CID}").json()["videos"][0]
    assert first["id"] == VID and first["published_at"] is not None and first["view_count"] == 1234


def test_playlist_page(env):
    client, *_ = env
    page = client.get("/api/playlist/PLBDA2E52FB1EF80C9").json()
    assert page["playlist"]["title"] == "World History" and len(page["videos"]) == 2
    assert client.get("/api/playlist/short").status_code == 400


def test_image_proxy_only_serves_youtube_image_hosts_and_caches(env):
    client, ctx, ex, seen = env
    ok = client.get("/api/img", params={"u": "//yt3.ggpht.com/av=s176"})          # protocol-relative
    assert ok.status_code == 200 and ok.content == b"AVATAR" and ok.headers["content-type"] == "image/png"
    client.get("/api/img", params={"u": "https://yt3.ggpht.com/av=s176"})
    assert sum("ggpht" in str(q.url) for q in seen) == 1                         # second came from cache
    assert client.get("/api/img", params={"u": "https://i.ytimg.com/vi/x/hq.jpg"}).content == b"JPEG"
    for bad in ("https://evil.example/x.jpg", "http://i.ytimg.com/x.jpg",
                "https://www.youtube.com/feeds/videos.xml?channel_id=UC1",         # allowlisted, not an image host
                "https://rr1.googlevideo.com/videoplayback", "file:///etc/passwd"):
        assert client.get("/api/img", params={"u": bad}).status_code == 400, bad


def test_list_endpoints_require_login(env):
    from fastapi.testclient import TestClient
    anon = TestClient(env[0].app)
    for url in ("/api/search?q=x", f"/api/channel/{CID}", "/api/playlist/PLBDA2E52FB1EF80C9", "/api/img?u=x"):
        assert anon.get(url).status_code == 401, url
