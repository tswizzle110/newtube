import httpx
import pytest
from fastapi.testclient import TestClient

from newtube.app import Context, create_app
from newtube.auth import Auth
from newtube.config import Settings
from newtube.db.connection import connect, migrate
from newtube.extract.base import (
    ChannelInfo, Comment, CommentPage, ExtractionError, ChannelSummary, Chapter, Format, PlaylistSummary, VideoInfo, VideoSummary, VideoUnavailable,
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
        self.comment_calls = []

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

    # ---- comments (synthetic): 45 top-level comments exist; threads 1-5 have 3 replies, thread 5 has none
    async def comments(self, video_id, sort="top", limit=20, *, replies_for=0, replies_per_thread=10,
                       background=False):
        self.comment_calls.append((sort, limit, replies_for))
        out = []
        for i in range(min(limit, 45)):
            cid = f"Ugx{i:07d}"
            out.append(Comment(cid, f"{sort} comment {i}", f"Author{i}", like_count=100 - i, time_text="2 days ago"))
            if i < replies_for and i != 4:
                out += [Comment(f"{cid}.r{j}", f"reply {j} to {i}", "Replier", parent=cid) for j in range(3)]
        return CommentPage(out, 4321)

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
        self.channel_calls = getattr(self, "channel_calls", []) + [limit]
        # like the real channel tab: entries carry no views / date / channel name
        vids = [VideoSummary(VID, "Upload one", channel_id, "YouTube", 607), VideoSummary("bbbbbbbbbbb", "Upload two", channel_id, "YouTube", 60)]
        return (ChannelInfo(channel_id, "YouTube", "@YouTube", "https://yt3.ggpht.com/av", "official", 46600000), vids[:limit])

    def uploads_list(self, channel_id):
        """The channel's uploads, newest first: any `new_uploads` on top of 5+ synthetic older ones."""
        n = getattr(self, "upload_count", 5)
        first = [10, 500, 30, 500, None]
        views = [first[i] if i < 5 else 1 + i % 40 for i in range(n)]
        base = [VideoSummary(f"vid{i:08d}", f"Video {i}", channel_id, "YouTube", 60 + i, v) for i, v in enumerate(views)]
        gone = getattr(self, "removed_ids", set())
        return [v for v in list(getattr(self, "new_uploads", [])) + base if v.id not in gone]

    async def recent_uploads(self, channel_id, limit=100, *, background=False):
        """One page of the newest uploads (what a cheap top-up reads)."""
        self.recent_calls = getattr(self, "recent_calls", 0) + 1
        if getattr(self, "recent_error", None):
            raise ExtractionError(self.recent_error)
        return self.uploads_list(channel_id)[:limit]

    async def channel_uploads(self, channel_id, limit=10_000, progress=None, *, background=False):
        """The whole uploads playlist, with view counts. Slow on demand, to test the progress flow."""
        import asyncio
        self.upload_calls = getattr(self, "upload_calls", 0) + 1
        self.scan_running = getattr(self, "scan_running", 0) + 1
        self.max_scans = max(getattr(self, "max_scans", 0), self.scan_running)
        try:
            vids = self.uploads_list(channel_id)
            if progress:
                progress(min(2, len(vids)), getattr(self, "uploads_total", len(vids)))
            await asyncio.sleep(getattr(self, "scan_delay", 0))
            if getattr(self, "scan_error", None):
                raise ExtractionError(self.scan_error)
            return vids, getattr(self, "uploads_total", len(vids))
        finally:
            self.scan_running -= 1

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


# ---- comments --------------------------------------------------------------------------------

V = "aaaaaaaaaaa"


def test_comments_first_page_has_more_and_estimate(env):
    client, ctx, ex, _ = env
    r = client.get(f"/api/comments/{V}").json()
    assert len(r["comments"]) == 20 and r["has_more"] is True and r["total_estimate"] == 4321
    assert r["comments"][0]["text"] == "top comment 0" and r["max_reply_threads"] == 10
    assert ex.comment_calls == [("top", 20, 0)]


def test_comments_cached_and_smaller_requests_are_sliced_from_cache(env):
    client, ctx, ex, _ = env
    client.get(f"/api/comments/{V}?limit=40")
    assert len(client.get(f"/api/comments/{V}?limit=20").json()["comments"]) == 20
    assert client.get(f"/api/comments/{V}?limit=20").json()["has_more"] is True   # 40 are held
    assert ex.comment_calls == [("top", 40, 0)]                                    # one fetch only


def test_load_more_refetches_bigger_and_stops_when_comments_run_out(env):
    client, ctx, ex, _ = env
    client.get(f"/api/comments/{V}?limit=20")
    r = client.get(f"/api/comments/{V}?limit=60").json()                           # only 45 exist
    assert len(r["comments"]) == 45 and r["has_more"] is False
    assert ex.comment_calls == [("top", 20, 0), ("top", 60, 0)]


def test_sorts_are_cached_separately_and_validated(env):
    client, ctx, ex, _ = env
    assert client.get(f"/api/comments/{V}?sort=new").json()["comments"][0]["text"] == "new comment 0"
    client.get(f"/api/comments/{V}?sort=top")
    assert [c[0] for c in ex.comment_calls] == ["new", "top"]
    assert client.get(f"/api/comments/{V}?sort=hot").status_code == 400
    assert client.get(f"/api/comments/{V}?limit=101").status_code == 422
    assert client.get("/api/comments/short").status_code == 400


def test_replies_need_loaded_comments_and_are_capped_at_ten_threads(env):
    client, ctx, ex, _ = env
    cid = lambda i: f"Ugx{i:07d}"
    assert client.get(f"/api/comments/{V}/replies/{cid(0)}").status_code == 404      # nothing loaded yet
    client.get(f"/api/comments/{V}?limit=20")
    assert client.get(f"/api/comments/{V}/replies/{cid(10)}").status_code == 409     # the 11th comment
    assert client.get(f"/api/comments/{V}/replies/{cid(10)}").json()["detail"].startswith("Replies are only")
    assert client.get(f"/api/comments/{V}/replies/{cid(9)}").status_code == 200       # the 10th is fine
    assert client.get(f"/api/comments/{V}/replies/not-a-loaded-comment").status_code == 404
    assert client.get(f"/api/comments/{V}/replies/x").status_code == 400


def test_expanding_a_thread_fetches_up_to_it_once_and_serves_earlier_ones_from_cache(env):
    client, ctx, ex, _ = env
    cid = lambda i: f"Ugx{i:07d}"
    client.get(f"/api/comments/{V}?limit=20")
    r3 = client.get(f"/api/comments/{V}/replies/{cid(2)}").json()
    assert [x["text"] for x in r3["replies"]] == ["reply 0 to 2", "reply 1 to 2", "reply 2 to 2"]
    assert r3["truncated"] is False
    assert ex.comment_calls[-1] == ("top", 3, 3)                       # walked threads 1-3 only
    n = len(ex.comment_calls)
    assert len(client.get(f"/api/comments/{V}/replies/{cid(0)}").json()["replies"]) == 3
    assert len(client.get(f"/api/comments/{V}/replies/{cid(1)}").json()["replies"]) == 3
    assert len(ex.comment_calls) == n                                  # no more requests
    assert client.get(f"/api/comments/{V}/replies/{cid(4)}").json()["replies"] == []   # a thread with none
    assert ex.comment_calls[-1] == ("top", 5, 5)                       # deeper: one bigger walk


def test_loading_more_comments_keeps_replies_already_fetched(env):
    client, ctx, ex, _ = env
    client.get(f"/api/comments/{V}?limit=20")
    client.get(f"/api/comments/{V}/replies/Ugx0000001")
    client.get(f"/api/comments/{V}?limit=40")
    n = len(ex.comment_calls)
    assert len(client.get(f"/api/comments/{V}/replies/Ugx0000001").json()["replies"]) == 3
    assert len(ex.comment_calls) == n


def test_comments_require_login_and_surface_extractor_failures(env):
    from fastapi.testclient import TestClient
    from newtube.extract.base import BotCheck
    client, ctx, ex, _ = env
    assert TestClient(client.app).get(f"/api/comments/{V}").status_code == 401

    async def boom(*a, **k):
        raise BotCheck("Sign in to confirm you're not a bot")
    ex.comments = boom
    assert client.get(f"/api/comments/{V}?sort=new").status_code == 503




# ---- channel sorting: Popular / Oldest are built from the channel's whole upload list ---------------

import time as _time


def titles(r):
    return [v["title"] for v in r["videos"]]


def sorted_page(client, cid, **params):
    """Ask for a sorted page and wait while the background scan reads the uploads."""
    q = "&".join(f"{k}={v}" for k, v in params.items())
    deadline = _time.time() + 10
    while True:
        r = client.get(f"/api/channel/{cid}?{q}")
        if r.status_code != 200 or "scanning" not in r.json():
            return r
        assert _time.time() < deadline, "scan never finished"
        _time.sleep(0.05)


def test_first_sorted_request_answers_at_once_with_progress_then_serves_from_cache(env):
    client, ctx, ex, _ = env
    ex.scan_delay = 0.4
    first = client.get(f"/api/channel/{CID}?sort=oldest").json()
    assert first["videos"] == [] and "scanning" in first                              # did not wait for the scan
    assert first["channel"]["title"] == "YouTube"
    _time.sleep(0.15)                                                                 # the next poll shows progress
    assert client.get(f"/api/channel/{CID}?sort=oldest").json()["scanning"] == {"done": 2, "total": 5}
    other = client.get(f"/api/channel/{CID}?sort=popular").json()                     # same scan is shared
    assert "scanning" in other and ex.upload_calls == 1
    done = sorted_page(client, CID, sort="oldest").json()
    assert titles(done) == ["Video 4", "Video 3", "Video 2", "Video 1", "Video 0"] and "scanning" not in done
    assert ex.upload_calls == 1 and done["total_videos"] == 5 and done["fetched_at"] > 0


def test_popular_ranks_by_views_and_ties_stay_newest_first(env):
    client, ctx, ex, _ = env
    pop = sorted_page(client, CID, sort="popular").json()
    assert titles(pop) == ["Video 1", "Video 3", "Video 2", "Video 0", "Video 4"]     # 500, 500, 30, 10, unknown last
    assert pop["videos"][0]["view_count"] == 500 and "notice" not in pop
    new = client.get(f"/api/channel/{CID}").json()
    assert titles(new) == ["Upload one", "Upload two"]                                # newest still uses the Videos tab


def test_oldest_is_exact_for_a_big_channel_and_paging_never_repeats_or_skips(env):
    client, ctx, ex, _ = env
    ex.upload_count = 130
    seen = []
    for offset in (0, 60, 120):
        page = sorted_page(client, CID, sort="oldest", offset=offset, limit=60).json()
        seen += [v["id"] for v in page["videos"]]
        assert page["has_more"] is (offset < 120)
    assert len(seen) == 130 and len(set(seen)) == 130
    assert seen[0] == "vid00000129" and seen[-1] == "vid00000000"                     # oldest first, all the way back
    assert ex.upload_calls == 1                                                       # paging cost nothing more


def test_a_big_channel_scan_is_cached_so_switching_sort_costs_nothing(env):
    client, ctx, ex, _ = env
    ex.upload_count = 130
    sorted_page(client, CID, sort="oldest")
    for sort in ("popular", "oldest", "popular"):
        assert "scanning" not in client.get(f"/api/channel/{CID}?sort={sort}").json()
    assert ex.upload_calls == 1


def test_beyond_the_scan_limit_popular_is_partial_and_oldest_is_unavailable(env, monkeypatch):
    from newtube.services import lists
    monkeypatch.setattr(lists, "SCAN_LIMIT", 5)
    client, ctx, ex, _ = env
    ex.uploads_total = 3214
    pop = sorted_page(client, CID, sort="popular").json()
    assert len(pop["videos"]) == 5 and "newest 5 of 3214" in pop["notice"]
    old = client.get(f"/api/channel/{CID}?sort=oldest").json()
    assert old["videos"] == [] and "3214 videos" in old["unavailable"]


def test_rescan_forces_a_fresh_read_of_the_uploads(env):
    client, ctx, ex, _ = env
    sorted_page(client, CID, sort="oldest")
    assert ex.upload_calls == 1
    ex.upload_count = 7
    assert "scanning" in client.get(f"/api/channel/{CID}?sort=oldest&refresh=true").json()   # refresh only on the FIRST request
    r = sorted_page(client, CID, sort="oldest").json()                                      # ...then just poll
    assert ex.upload_calls == 2 and len(r["videos"]) == 7


def test_a_failed_scan_is_reported_once_and_the_next_attempt_starts_fresh(env):
    client, ctx, ex, _ = env
    ex.scan_error = "Sign in to confirm you're not a bot"
    deadline = _time.time() + 10
    while True:
        r = client.get(f"/api/channel/{CID}?sort=oldest")
        if r.status_code != 200:
            break
        assert _time.time() < deadline
        _time.sleep(0.05)
    assert r.status_code == 502 and "not a bot" in r.json()["detail"]
    ex.scan_error = None
    again = sorted_page(client, CID, sort="oldest")
    assert again.status_code == 200 and ex.upload_calls == 2


def test_only_one_scan_runs_at_a_time(env):
    client, ctx, ex, _ = env
    ex.scan_delay = 0.3
    other = "UC" + "z" * 22
    client.get(f"/api/channel/{CID}?sort=oldest"); client.get(f"/api/channel/{other}?sort=oldest")
    sorted_page(client, CID, sort="oldest"); sorted_page(client, other, sort="oldest")
    assert ex.upload_calls == 2 and ex.max_scans == 1


def test_newest_pages_by_offset_and_reports_whether_more_exist(env):
    client, ctx, ex, _ = env
    r = client.get(f"/api/channel/{CID}?offset=1&limit=1").json()
    assert titles(r) == ["Upload two"] and ex.channel_calls == [2]                    # fetched 2 deep, sliced
    assert client.get(f"/api/channel/{CID}?limit=30").json()["has_more"] is False     # the list ended before 30

def test_channel_sort_and_limit_are_validated(env):
    client, *_ = env
    assert client.get(f"/api/channel/{CID}?sort=best").status_code == 400
    assert client.get(f"/api/channel/{CID}?limit=0").status_code == 422
    assert client.get(f"/api/channel/{CID}?limit=501").status_code == 422
    assert client.get(f"/api/channel/{CID}?offset=-1").status_code == 422


def test_newest_limit_is_part_of_its_cache_key(env):
    client, ctx, ex, _ = env
    assert len(client.get(f"/api/channel/{CID}?limit=1").json()["videos"]) == 1
    assert len(client.get(f"/api/channel/{CID}?limit=30").json()["videos"]) == 2
    assert ex.channel_calls == [1, 30]


# ---- the stored upload list: kept, topped up cheaply, refreshed monthly --------------------------------

def wait_scans(client, ctx, cid=CID):
    deadline = _time.time() + 10
    while ctx.lists._scans.get(cid) is not None and not ctx.lists._scans[cid].finished:
        assert _time.time() < deadline
        _time.sleep(0.02)
    _time.sleep(0.05)


def test_the_scan_is_kept_in_the_database_and_survives_a_restart(env):
    from newtube.services.lists import ListService
    client, ctx, ex, _ = env
    ex.upload_count = 40
    sorted_page(client, CID, sort="oldest")
    assert ctx.conn.execute("SELECT count(*) FROM channel_uploads WHERE channel_id=?", (CID,)).fetchone()[0] == 40
    ctx.lists = ListService(ctx.conn, ex)                       # a "restarted" server: nothing cached in memory
    r = client.get(f"/api/channel/{CID}?sort=oldest").json()
    assert "scanning" not in r and len(r["videos"]) == 30
    assert ex.upload_calls == 1                                  # no second scan after the restart


def test_opening_a_channel_after_a_while_adds_new_uploads_with_one_request(env):
    client, ctx, ex, _ = env
    sorted_page(client, CID, sort="popular")
    ex.new_uploads = [VideoSummary("new00000001", "Brand new", CID, "YouTube", 99, 1000)]
    ctx.conn.execute("UPDATE channel_scans SET topped_up_at=0")
    pop = client.get(f"/api/channel/{CID}?sort=popular").json()
    assert titles(pop)[0] == "Brand new" and pop["total_videos"] == 6      # picked up, ranked by its views
    old = client.get(f"/api/channel/{CID}?sort=oldest").json()
    assert titles(old)[-1] == "Brand new"                                  # ...and it is the NEWEST, so last in Oldest
    assert ex.recent_calls == 1 and ex.upload_calls == 1                   # one cheap read, no rescan
    client.get(f"/api/channel/{CID}?sort=popular")
    assert ex.recent_calls == 1                                            # not again within hours


def test_several_top_ups_keep_newest_first_ordering(env):
    client, ctx, ex, _ = env
    ex.uploads_total = 5
    sorted_page(client, CID, sort="oldest")
    for n in (1, 2):
        ex.new_uploads = [VideoSummary(f"new{n:08d}", f"New {n}", CID, "YouTube", 9, 7)] + getattr(ex, "new_uploads", [])
        ctx.conn.execute("UPDATE channel_scans SET topped_up_at=0")
        client.get(f"/api/channel/{CID}?sort=oldest")
    old = titles(client.get(f"/api/channel/{CID}?sort=oldest").json())
    assert old[-2:] == ["New 1", "New 2"]                                  # New 2 is the newest of all


def test_a_failed_top_up_serves_the_stored_list_and_tries_again_next_time(env):
    client, ctx, ex, _ = env
    sorted_page(client, CID, sort="oldest")
    ctx.conn.execute("UPDATE channel_scans SET topped_up_at=0")
    ex.recent_error = "network down"
    r = client.get(f"/api/channel/{CID}?sort=oldest")
    assert r.status_code == 200 and len(r.json()["videos"]) == 5
    assert ctx.conn.execute("SELECT topped_up_at FROM channel_scans").fetchone()[0] == 0   # not marked as done
    ex.recent_error = None
    client.get(f"/api/channel/{CID}?sort=oldest")
    assert ctx.conn.execute("SELECT topped_up_at FROM channel_scans").fetchone()[0] > 0


def test_a_whole_page_of_unseen_uploads_triggers_a_background_rescan_while_the_old_list_is_served(env):
    client, ctx, ex, _ = env
    ex.scan_delay = 0.2
    sorted_page(client, CID, sort="oldest")
    ex.new_uploads = [VideoSummary(f"gap{i:08d}", f"Gap {i}", CID, "YouTube", 9, 1) for i in range(120)]
    ctx.conn.execute("UPDATE channel_scans SET topped_up_at=0")
    r = client.get(f"/api/channel/{CID}?sort=oldest").json()
    assert r["refreshing"] is True and len(r["videos"]) == 5 and "scanning" not in r       # the old list, meanwhile
    wait_scans(client, ctx)
    r2 = client.get(f"/api/channel/{CID}?sort=oldest").json()
    assert r2["refreshing"] is False and r2["total_videos"] == 125 and ex.upload_calls == 2


def test_the_list_is_re_read_in_the_background_after_30_days(env):
    client, ctx, ex, _ = env
    sorted_page(client, CID, sort="oldest")
    ctx.conn.execute("UPDATE channel_scans SET scanned_at=?", (int(_time.time()) - 31 * 86400,))
    ex.upload_count = 8
    r = client.get(f"/api/channel/{CID}?sort=oldest").json()
    assert r["refreshing"] is True and len(r["videos"]) == 5                               # no waiting
    wait_scans(client, ctx)
    r2 = client.get(f"/api/channel/{CID}?sort=oldest").json()
    assert r2["total_videos"] == 8 and r2["refreshing"] is False and ex.upload_calls == 2
    assert r2["fetched_at"] > _time.time() - 60


def test_a_full_rescan_drops_videos_that_were_deleted(env):
    client, ctx, ex, _ = env
    sorted_page(client, CID, sort="oldest")
    ex.removed_ids = {"vid00000002"}
    assert "scanning" in client.get(f"/api/channel/{CID}?sort=oldest&refresh=true").json()   # explicit: shows progress
    r = sorted_page(client, CID, sort="oldest").json()
    assert "Video 2" not in titles(r) and r["total_videos"] == 4


def test_a_failed_background_refresh_does_not_take_the_stored_list_away(env):
    client, ctx, ex, _ = env
    sorted_page(client, CID, sort="oldest")
    ctx.conn.execute("UPDATE channel_scans SET scanned_at=0")
    ex.scan_error = "boom"
    client.get(f"/api/channel/{CID}?sort=oldest")
    wait_scans(client, ctx)
    r = client.get(f"/api/channel/{CID}?sort=oldest")
    assert r.status_code == 200 and len(r.json()["videos"]) == 5                           # the error is logged, not shown


def test_recommendations_endpoint_returns_cards_with_reasons(env):
    import time
    client, ctx, ex, seen = env
    now = int(time.time())
    ctx.conn.execute("INSERT INTO channels(id,title,created_at) VALUES (?,?,1)", (CID, "Chan"))
    ctx.conn.execute("INSERT INTO subscriptions VALUES (?,1)", (CID,))
    ctx.conn.execute("INSERT INTO videos(id,channel_id,title,published_at,first_seen_at) VALUES (?,?,?,?,1)", (VID, CID, "T", now - 3600))
    r = client.get("/api/recommendations?limit=5")
    assert r.status_code == 200
    (card,) = r.json()
    assert card["id"] == VID and card["channel_title"] == "Chan" and card["completed"] is False
    assert card["why"]["score"] > 0 and {x["label"] for x in card["why"]["reasons"]} >= {"From a channel you subscribe to"}
    assert not [q for q in seen if "youtube" in str(q.url)]          # computed locally: nothing was asked of YouTube


# ---- your lists (Liked / Watch later) over the API ---------------------------------------------------------

def test_like_and_watch_later_through_the_api(env):
    client, ctx, ex, seen = env
    assert client.get(f"/api/video/{VID}").json()["liked"] is False
    r = client.post("/api/mylists/liked/items", json={"video_id": VID})
    assert r.status_code == 200 and r.json() == {"added": True, "liked": True, "watch_later": False, "playlists": []}
    assert client.post("/api/mylists/liked/items", json={"video_id": VID}).json()["added"] is False
    client.post("/api/mylists/watch_later/items", json={"video_id": VID})
    v = client.get(f"/api/video/{VID}").json()
    assert v["liked"] is True and v["watch_later"] is True
    page = client.get("/api/mylists/liked").json()
    assert page["list"]["count"] == 1 and page["videos"][0]["id"] == VID and page["videos"][0]["title"] == v["title"]
    assert client.get(f"/api/mylists/for-video/{VID}").json()["liked"] is True
    assert client.delete(f"/api/mylists/liked/items/{VID}").json() == {"removed": True, "liked": False, "watch_later": True, "playlists": []}
    assert [x["name"] for x in client.get("/api/mylists").json()] == ["Liked videos", "Watch later"]


def test_lists_api_rejects_bad_input(env):
    client, ctx, ex, seen = env
    assert client.get("/api/mylists/nope").status_code == 404
    assert client.post("/api/mylists/liked/items", json={"video_id": "short"}).status_code == 400
    assert client.post("/api/mylists/999/items", json={"video_id": VID}).status_code == 404
    assert client.delete("/api/mylists/liked/items/not-an-id").status_code == 400
