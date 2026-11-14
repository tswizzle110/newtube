"""The "For you" list. Computed from the local database on every request; no outbound traffic."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends

from ..app import Context
import time

from ..ml.embedder import load_embedder
from ..ml.taste import scorer
from ..recommend.baseline import recommend
from .deps import get_ctx

router = APIRouter(prefix="/api")


_embedders: dict[tuple, Any] = {}


def _embedder(settings):
    """The configured embedder, loaded once (a real model takes seconds and a lot of memory to load)."""
    key = (settings.embedding_model, settings.embedding_device, str(settings.data_dir))
    if key not in _embedders:
        _embedders[key] = load_embedder(settings)
    return _embedders[key]


def compute(ctx: Context, limit: int) -> list[dict[str, Any]]:
    taste = None
    try:
        taste = scorer(ctx.conn, _embedder(ctx.settings).name, int(time.time()))
    except Exception:  # noqa: BLE001 - a missing model must not take the whole page down: rank without it
        pass
    return [r.to_dict() for r in recommend(ctx.conn, limit=limit, taste=taste)]


@router.get("/recommendations")
async def recommendations(limit: int = 200, ctx: Context = Depends(get_ctx)) -> list[dict[str, Any]]:
    """The whole list is returned at once (not paged): the ranking depends on what you were just shown, so
    separate pages would reshuffle between requests. It is worked out on a worker thread: on a big library it takes a
    while, and the server must keep answering (a video that is starting) in the meantime."""
    return await asyncio.to_thread(compute, ctx, min(max(limit, 1), 300))
