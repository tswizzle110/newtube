"""The "For you" list. Computed from the local database on every request; no outbound traffic."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..app import Context
import time

from ..ml.embedder import load_embedder
from ..ml.taste import scorer
from ..recommend.baseline import recommend
from .deps import get_ctx

router = APIRouter(prefix="/api")


@router.get("/recommendations")
async def recommendations(limit: int = 200, ctx: Context = Depends(get_ctx)) -> list[dict[str, Any]]:
    """The whole list is returned at once (not paged): the ranking depends on what you were just shown, so
    separate pages would reshuffle between requests."""
    taste = None
    try:
        taste = scorer(ctx.conn, load_embedder(ctx.settings).name, int(time.time()))
    except Exception:  # noqa: BLE001 - a missing model must not take the whole page down: rank without it
        pass
    return [r.to_dict() for r in recommend(ctx.conn, limit=min(max(limit, 1), 300), taste=taste)]
