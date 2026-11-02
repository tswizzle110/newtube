"""Real newtube app; only YouTube is faked (extractor + a googlevideo that serves local files)."""
import hashlib, json, re, sys, tempfile
from pathlib import Path
import httpx, uvicorn

from newtube.app import Context, create_app
from newtube.auth import Auth
from newtube.config import Settings
from newtube.db.connection import connect, migrate
from newtube.extract.base import (
    Caption, ChannelInfo, ChannelSummary, Chapter, Comment, CommentPage, Format, PlaylistSummary, VideoInfo, VideoSummary,
)
from newtube.media.diskcache import DiskCache
from newtube.net.http import OutboundClient
from newtube.net.ratelimit import RateLimiter
from newtube.services.videos import VideoService

MEDIA = Path(sys.argv[1]); PORT = int(sys.argv[2])
FILES = {"v360": "v360.mp4", "v180": "v180.mp4", "aud": "a.m4a", "mux": "muxed.mp4",
         "vp9a": "vp9a.webm", "vp9b": "vp9b.webm"}
hits = []


class Body(httpx.AsyncByteStream):
    def __init__(self, b): self.b = b
    async def __aiter__(self): yield self.b


SB_VIDEO = "e2eSPONSOR1"
SB_HASH = hashlib.sha256(SB_VIDEO.encode()).hexdigest()


def _seg(cat, a, b, uuid, action="skip"):
    # real SponsorBlock segment ids are 64-character hashes, so pad ours to look the same
    return {"category": cat, "actionType": action, "segment": [a, b], "UUID": uuid.ljust(64, "0"), "videoDuration": 24,
            "locked": 0, "votes": 5, "description": ""}


sb_posts = []      # what SponsorBlock was sent (for the privacy assertions)


def sponsorblock(req):
    if req.method == "POST":
        sb_posts.append({"path": req.url.path, "body": json.loads(req.content)})
        return httpx.Response(200, text="OK")
    if req.method == "GET" and req.url.path == f"/api/skipSegments/{SB_HASH[:4]}":
        return httpx.Response(200, json=[
            # another video that shares the 4-char prefix: must be ignored
            {"videoID": "zzzzzzzzzzz", "hash": "x" * 64, "segments": [_seg("sponsor", 1, 3, "other")]},
            {"videoID": SB_VIDEO, "hash": SB_HASH, "segments": [
                _seg("sponsor", 4, 6, "sp1"), _seg("intro", 10, 12, "in1"), _seg("music_offtopic", 14, 15, "mo1"),
                _seg("poi_highlight", 20, 20, "po1", "poi"), _seg("selfpromo", 0, 0, "fl1", "full")]}])
    return httpx.Response(404)


def handler(req: httpx.Request) -> httpx.Response:
    u = str(req.url)
    hits.append((req.url.path, req.headers.get("range")))
    if "sponsor.ajay.app" in u:
        return sponsorblock(req)
    if "googlevideo.com" in u:
        data = (MEDIA / FILES[req.url.path.strip("/")]).read_bytes()
        m = re.match(r"bytes=(\d+)-(\d*)", req.headers.get("range", "bytes=0-"))
        a = int(m.group(1)); b = int(m.group(2)) if m.group(2) else len(data) - 1
        b = min(b, len(data) - 1)
        return httpx.Response(206, stream=Body(data[a:b + 1]), headers={
            "content-type": "video/mp4", "content-length": str(b - a + 1),
            "content-range": f"bytes {a}-{b}/{len(data)}", "accept-ranges": "bytes"})
    if "timedtext" in u:
        return httpx.Response(200, text=(MEDIA / "en.vtt").read_text())
    if "ytimg.com" in u or "ggpht.com" in u:   # any image: a valid 1x1 PNG
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
    return httpx.Response(404)


PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360f8cfc0f01f0005000201a2a3c2a40000000049454e44ae426082")


class Ex:
    async def video_info(self, vid, *, background=False):
        gv = lambda n: f"https://rr1.googlevideo.com/{n}?sig=x"
        if vid == "e2eVP9VID01":      # offers H.264 up to 1080p and VP9 "2160p"/"1440p" (stand-in files)
            fm = [Format("137", gv("v360"), "mp4", "avc1.4d401e", None, height=1080, width=1920, fps=25, tbr=4000, dynamic_range="SDR"),
                  Format("313", gv("vp9a"), "webm", "vp09.00.40.08", None, height=2160, width=3840, fps=25, tbr=15000, dynamic_range="SDR"),
                  Format("271", gv("vp9b"), "webm", "vp09.00.40.08", None, height=1440, width=2560, fps=60, tbr=6000, dynamic_range="SDR"),
                  Format("140", gv("aud"), "m4a", None, "mp4a.40.2", tbr=64, language="en-US", language_preference=10)]
        elif vid == "e2eMUXED001":
            fm = [Format("18", gv("mux"), "mp4", "avc1.4d401e", "mp4a.40.2", height=360, width=640, tbr=500)]
        else:
            fm = [Format("134", gv("v360"), "mp4", "avc1.4d401e", None, height=360, width=640, fps=25, tbr=800),
                  Format("133", gv("v180"), "mp4", "avc1.4d401e", None, height=180, width=320, fps=25, tbr=250),
                  Format("140-0", gv("aud"), "m4a", None, "mp4a.40.2", tbr=64, language="en-US",
                         language_preference=10, note="English (US) original (default), medium"),
                  Format("140-1", gv("aud"), "m4a", None, "mp4a.40.2", tbr=64, language="es-US",
                         language_preference=-1, note="Spanish (US), medium"),
                  Format("140-drc", gv("aud"), "m4a", None, "mp4a.40.2", tbr=64, language="en-US",
                         language_preference=10, note="English (US) original (default), medium, DRC"),
                  Format("18", gv("mux"), "mp4", "avc1.4d401e", "mp4a.40.2", height=360, width=640, tbr=500)]
        return VideoInfo(vid, "E2E test video", "UC" + "a" * 22, "Test Channel", "A description\nwith lines", 24,
                         1_700_000_000, 1234, None, [], None, False, 0,
                         chapters=[Chapter(0, "Intro"), Chapter(8, "Middle part"), Chapter(16, "Outro")],
                         captions=[Caption("en", "English", "https://www.youtube.com/api/timedtext?x=1", "vtt")],
                         formats=fm, fetched_at=1)
    async def resolve_channel(self, ref, *, background=False):
        return ChannelInfo(ref, "E2E Channel", "@e2e", "https://yt3.ggpht.com/av", None, 1200)

    async def comments(self, video_id, sort="top", limit=20, *, replies_for=0, replies_per_thread=10, background=False):
        out = []
        for i in range(min(limit, 45)):
            cid = f"Ugx{i:07d}"
            text = f"{sort} comment {i}" + (" see 0:10 and https://example.org/page" if i == 0 else "")
            out.append(Comment(cid, text, f"Author{i}", like_count=2000 - i, time_text="2 days ago",
                               author_id="UC" + "a" * 22 if i == 0 else None, is_pinned=(i == 0)))
            if i < replies_for:
                out += [Comment(f"{cid}.r{j}", f"reply {j} to {i}", "Replier", parent=cid) for j in range(2)]
        return CommentPage(out, 4321)

    async def search(self, query, kind="video", limit=20, *, background=False):
        cid = "UC" + "a" * 22
        return {"video": [VideoSummary("e2eVIDEO001", "Search result video", cid, "E2E Channel", 24, 5000)],
                "channel": [ChannelSummary(cid, "E2E Channel", "@e2e", "A test channel", "//yt3.ggpht.com/av=s176", 1200)],
                "playlist": [PlaylistSummary("PLe2eplaylist00", "E2E playlist", "E2E Channel", None,
                                             "https://i.ytimg.com/vi/e2eVIDEO001/hq720.jpg")]}[kind]

    IDS = {"A": "e2eVIDEO001", "B": "e2eMUXED001", "C": "e2eSPONSOR1"}

    async def channel_page(self, channel_id, limit=30, *, background=False):       # the Videos tab: newest first, no views
        vids = [VideoSummary(self.IDS[k], f"Upload {k}", channel_id, "E2E Channel", 24) for k in "ABC"]
        return (ChannelInfo(channel_id, "E2E Channel", "@e2e", "https://yt3.ggpht.com/av", "A test channel", 1200), vids[:limit])

    def uploads_list(self, channel_id):
        views = {"A": 100, "B": 5000, "C": 300}
        vids = [VideoSummary(self.IDS[k], f"Upload {k}", channel_id, "E2E Channel", 24, views[k]) for k in "ABC"]
        return vids + [VideoSummary(f"fil{i:08d}", f"Filler {i:03d}", channel_id, "E2E Channel", 24, 1 + i % 50) for i in range(127)]

    async def recent_uploads(self, channel_id, limit=100, *, background=False):
        return self.uploads_list(channel_id)[:limit]

    async def channel_uploads(self, channel_id, limit=10_000, progress=None, *, background=False):
        """The uploads playlist, newest first, with views: 3 real videos then 127 fillers, read slowly in 5 pages."""
        import asyncio
        vids = self.uploads_list(channel_id)
        for page in range(1, 6):
            await asyncio.sleep(0.35)
            if progress:
                progress(min(page * 26, 130), 130)
        return vids, 130

    async def playlist_page(self, playlist_id, limit=100, *, background=False):
        cid = "UC" + "a" * 22
        return (PlaylistSummary(playlist_id, "E2E playlist", "E2E Channel", 2),
                [VideoSummary("e2eVIDEO001", "First", cid, "E2E Channel", 24),
                 VideoSummary("e2eMUXED001", "Second", cid, "E2E Channel", 24)])


tmp = Path(tempfile.mkdtemp())
s = Settings(data_dir=tmp)
conn = connect(tmp / "t.db"); migrate(conn)
lim = RateLimiter(0, 0, 100)
auth = Auth(tmp / "auth.json"); auth.set_password("e2e-password")
ex = Ex()
ctx = Context(s, conn, lim, OutboundClient(s, lim, transport=httpx.MockTransport(handler)), ex,
              VideoService(conn, ex), DiskCache(tmp / "img", 10**7), auth, run_scheduler=False)
app = create_app(ctx)

# ---- harness-only introspection endpoints. Sandbox only: NOT part of the real app.
@app.get("/__hits")
async def _hits():
    return hits


@app.get("/__db")
async def _db():
    return {"progress": [dict(r) for r in conn.execute("SELECT * FROM video_progress")],
            "sessions": [dict(r) for r in conn.execute(
                "SELECT id, video_id, intervals, max_position, duration, ended_at FROM watch_sessions")]}


@app.get("/__events")
async def _events():
    return [dict(r) for r in conn.execute("SELECT type, video_id, channel_id, payload FROM events ORDER BY id")]


@app.get("/__sbposts")
async def _sbposts():
    return sb_posts


for _ in range(4):     # these must come before the app's catch-all route
    app.router.routes.insert(0, app.router.routes.pop())

uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
