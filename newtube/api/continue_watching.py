"""Continue watching on Home. Local only."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..app import Context
from ..db.queries import continue_watching as cw
from ..db.queries.events import log_event
from .deps import get_ctx, valid_video_id

router = APIRouter(prefix="/api/continue")


@router.get("")
async def continue_list(limit: int = 60, ctx: Context = Depends(get_ctx)) -> list[dict[str, Any]]:
    return cw.started(ctx.conn, min(max(limit, 1), 200))


@router.post("/{video_id}/done")
async def mark_watched(video_id: str, ctx: Context = Depends(get_ctx)) -> dict[str, bool]:
    """"Mark as watched": it leaves Continue watching and counts as finished (it stays in History)."""
    done = cw.mark_watched(ctx.conn, valid_video_id(video_id))
    if done:
        log_event(ctx.conn, "mark_watched", video_id=video_id)
    return {"done": done}
