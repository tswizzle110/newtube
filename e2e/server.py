"""Real newtube app; only YouTube is faked (extractor + a googlevideo that serves local files)."""
import re, sys, tempfile
from pathlib import Path
import httpx, uvicorn

from newtube.app import Context, create_app
from newtube.auth import Auth
from newtube.config import Settings
from newtube.db.connection import connect, migrate
from newtube.extract.base import Caption, Chapter, Format, VideoInfo
from newtube.media.diskcache import DiskCache
from newtube.net.http import OutboundClient
from newtube.net.ratelimit import RateLimiter
from newtube.services.videos import VideoService

MEDIA = Path(sys.argv[1]); PORT = int(sys.argv[2])
FILES = {"v360": "v360.mp4", "v180": "v180.mp4", "aud": "a.m4a", "mux": "muxed.mp4"}
hits = []


class Body(httpx.AsyncByteStream):
    def __init__(self, b): self.b = b
    async def __aiter__(self): yield self.b


def handler(req: httpx.Request) -> httpx.Response:
    u = str(req.url)
    hits.append((req.url.path, req.headers.get("range")))
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
    return httpx.Response(404)


class Ex:
    async def video_info(self, vid, *, background=False):
        gv = lambda n: f"https://rr1.googlevideo.com/{n}?sig=x"
        if vid == "e2eMUXED001":
            fm = [Format("18", gv("mux"), "mp4", "avc1.4d401e", "mp4a.40.2", height=360, width=640, tbr=500)]
        else:
            fm = [Format("134", gv("v360"), "mp4", "avc1.4d401e", None, height=360, width=640, fps=25, tbr=800),
                  Format("133", gv("v180"), "mp4", "avc1.4d401e", None, height=180, width=320, fps=25, tbr=250),
                  Format("140", gv("aud"), "m4a", None, "mp4a.40.2", tbr=64),
                  Format("18", gv("mux"), "mp4", "avc1.4d401e", "mp4a.40.2", height=360, width=640, tbr=500)]
        return VideoInfo(vid, "E2E test video", "UC" + "a" * 22, "Test Channel", "A description\nwith lines", 24,
                         1_700_000_000, 1234, None, [], None, False, 0,
                         chapters=[Chapter(0, "Intro"), Chapter(8, "Middle part"), Chapter(16, "Outro")],
                         captions=[Caption("en", "English", "https://www.youtube.com/api/timedtext?x=1", "vtt")],
                         formats=fm, fetched_at=1)
    async def resolve_channel(self, ref, *, background=False): raise NotImplementedError


tmp = Path(tempfile.mkdtemp())
s = Settings(data_dir=tmp)
conn = connect(tmp / "t.db"); migrate(conn)
lim = RateLimiter(0, 0, 100)
auth = Auth(tmp / "auth.json"); auth.set_password("e2e-password")
ex = Ex()
ctx = Context(s, conn, lim, OutboundClient(s, lim, transport=httpx.MockTransport(handler)), ex,
              VideoService(conn, ex), DiskCache(tmp / "img", 10**7), auth, run_scheduler=False)
app = create_app(ctx)

@app.get("/__hits")      # harness-only introspection (sandbox only)
async def _hits(): return hits
@app.get("/__db")
async def _db():
    return {"progress": [dict(r) for r in conn.execute("select * from video_progress")],
            "sessions": [dict(r) for r in conn.execute("select id, video_id, intervals, max_position, duration, ended_at from watch_sessions")]}
@app.get("/__db")
async def _db():
    return {"progress": [dict(r) for r in conn.execute("select * from video_progress")],
            "sessions": [dict(r) for r in conn.execute("select id, video_id, intervals, max_position, duration, ended_at from watch_sessions")]}
for _ in range(2):
    app.router.routes.insert(0, app.router.routes.pop())
@app.get("/__db")
async def _db():
    return {"progress": [dict(r) for r in conn.execute("select * from video_progress")],
            "sessions": [dict(r) for r in conn.execute("select id, video_id, intervals, max_position, duration, ended_at from watch_sessions")]}
for _ in range(2):
    app.router.routes.insert(0, app.router.routes.pop())   # must precede the SPA catch-all

uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
