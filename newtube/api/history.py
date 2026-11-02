"""Watch history page. Local only."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..app import Context
from ..db.queries import history
from .deps import get_ctx, valid_video_id

router = APIRouter(prefix="/api/history")


@router.get("")
async def get_history(limit: int = 60, offset: int = 0, q: str = "",
                      ctx: Context = Depends(get_ctx)) -> list[dict[str, Any]]:
    return history.page(ctx.conn, min(max(limit, 1), 200), max(offset, 0), q[:100])


@router.delete("/{video_id}")
async def remove_from_history(video_id: str, ctx: Context = Depends(get_ctx)) -> dict[str, bool]:
    history.remove(ctx.conn, valid_video_id(video_id))
    return {"removed": True}
