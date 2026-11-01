"""FastAPI application factory and shared context."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import FastAPI

from .config import Settings, load_settings
from .db.connection import connect, migrate
from .extract.base import Extractor
from .jobs.scheduler import Scheduler
from .media.diskcache import DiskCache
from .net.http import OutboundClient
from .net.ratelimit import RateLimiter
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
    run_scheduler: bool = True


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
                   DiskCache(settings.cache_dir / "images", settings.cache_max_bytes))


def create_app(ctx: Context | None = None) -> FastAPI:
    from .api import media, subscriptions, videos

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        c = ctx or build_context()
        app.state.ctx = c
        task = None
        if c.run_scheduler:
            task = asyncio.create_task(Scheduler(c.settings, c.conn, c.http).run())
        try:
            yield
        finally:
            if task:
                task.cancel()
            await c.http.aclose()

    app = FastAPI(title="newtube", lifespan=lifespan, docs_url=None, redoc_url=None)
    if ctx:  # tests construct the context themselves; make it available without lifespan too
        app.state.ctx = ctx
    app.include_router(media.router)
    app.include_router(videos.router)
    app.include_router(subscriptions.router)
    return app
