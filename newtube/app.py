"""FastAPI application factory and shared context."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from .auth import Auth
from .config import Settings, load_settings
from .db.connection import connect, migrate
from .extract.base import Extractor
from .jobs.scheduler import Scheduler
from .media.diskcache import DiskCache
from .net.http import OutboundClient
from .net.ratelimit import RateLimiter
from .services.comments import CommentService
from .sponsorblock.contribute import Contributor
from .sponsorblock.provider import SponsorBlockApi
from .sponsorblock.service import SponsorBlockService
from .services.lists import ListService
from .services.videos import VideoService

log = logging.getLogger("newtube")


@dataclass
class Context:
    settings: Settings
    conn: sqlite3.Connection
    limiter: RateLimiter
    http: OutboundClient
    extractor: Extractor
    videos: VideoService
    images: DiskCache
    auth: Auth
    run_scheduler: bool = True
    lists: ListService | None = None
    comments: CommentService | None = None
    sponsorblock: SponsorBlockService | None = None
    sb_contrib: Contributor | None = None
    sb_limiter: RateLimiter | None = None

    def __post_init__(self) -> None:
        if self.lists is None:
            self.lists = ListService(self.conn, self.extractor)
        if self.comments is None:
            self.comments = CommentService(self.conn, self.extractor)
        if self.sb_limiter is None:
            # SponsorBlock is a different host from YouTube: it gets its own, lighter limiter.
            self.sb_limiter = RateLimiter(0.25, 0.1, 10**9)
        if self.sponsorblock is None:
            self.sponsorblock = SponsorBlockService(self.conn, SponsorBlockApi(self.http, self.sb_limiter))
        if self.sb_contrib is None:
            self.sb_contrib = Contributor(self.conn, self.http, self.sb_limiter)

def build_context(settings: Settings | None = None) -> Context:
    from .extract.ytdlp import YtDlpExtractor

    settings = settings or load_settings()
    conn = connect(settings.db_path)
    migrate(conn)
    limiter = RateLimiter(settings.min_request_interval, settings.request_jitter,
                          settings.background_daily_cap)
    extractor = YtDlpExtractor(settings, limiter)
    return Context(settings, conn, limiter, OutboundClient(settings, limiter), extractor,
                   VideoService(conn, extractor),
                   DiskCache(settings.cache_dir / "images", settings.cache_max_bytes),
                   Auth(settings.data_dir / "auth.json"))


def create_app(ctx: Context | None = None) -> FastAPI:
    from .api import auth as auth_api
    from .api import comments, events, lists, media, mylists, playback, recommendations, sponsorblock, subscriptions, videos
    from .security import GuardMiddleware

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        c = ctx or build_context()
        app.state.ctx = c
        task = None
        if c.run_scheduler:
            task = asyncio.create_task(Scheduler(c.settings, c.conn, c.http, extractor=c.extractor).run())
        try:
            yield
        finally:
            if task:
                task.cancel()
            from .net import audit
            if audit.active():
                audit.active().flush(c.conn)
            await c.http.aclose()

    app = FastAPI(title="newtube", lifespan=lifespan, docs_url=None, redoc_url=None)
    if ctx:  # tests construct the context themselves; make it available without lifespan too
        app.state.ctx = ctx
    app.add_middleware(GuardMiddleware)
    app.include_router(auth_api.router)
    app.include_router(events.router)
    app.include_router(lists.router)
    app.include_router(comments.router)
    app.include_router(sponsorblock.router)
    app.include_router(playback.router)
    app.include_router(media.router)
    app.include_router(videos.router)
    app.include_router(subscriptions.router)
    app.include_router(recommendations.router)
    app.include_router(mylists.router)
    _mount_frontend(app, (ctx.settings if ctx else load_settings()).frontend_dir)
    return app


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    """Serve the built Svelte app. Unknown non-API paths get index.html (client-side routing)."""
    dist = Path(dist).resolve()
    index = dist / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> Response:
        if path.startswith("api/"):
            return JSONResponse({"detail": "not found"}, status_code=404)
        if not index.is_file():
            return PlainTextResponse(
                "Frontend not built. Run: cd frontend && npm install && npm run build", status_code=503)
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(dist):
            immutable = path.startswith("assets/")   # Vite content-hashes these filenames
            return FileResponse(candidate, headers={"Cache-Control": (
                "public, max-age=31536000, immutable" if immutable else "no-cache")})
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
