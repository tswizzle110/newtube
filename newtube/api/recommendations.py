"""The "For you" list. Computed from the local database on every request; no outbound traffic."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..app import Context
from ..recommend.baseline import recommend
from .deps import get_ctx

router = APIRouter(prefix="/api")


@router.get("/recommendations")
async def recommendations(limit: int = 60, ctx: Context = Depends(get_ctx)) -> list[dict[str, Any]]:
    """The whole list is returned at once (not paged): the ranking depends on what you were just shown, so
    separate pages would reshuffle between requests."""
    return [r.to_dict() for r in recommend(ctx.conn, limit=min(max(limit, 1), 120))]
