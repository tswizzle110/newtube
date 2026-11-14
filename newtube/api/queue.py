"""The play queue."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..app import Context
from ..db.queries import queue as q
from .deps import get_ctx, valid_video_id

router = APIRouter(prefix="/api/queue")


class AddIn(BaseModel):
    video_id: str
    next: bool = False            # "Play next": in front of everything already queued


class OrderIn(BaseModel):
    video_ids: list[str] = Field(max_length=500)


def _state(ctx: Context, limit: int = 100) -> dict[str, Any]:
    return {"count": q.count(ctx.conn), "videos": q.items(ctx.conn, min(max(limit, 1), 500))}


@router.get("")
async def get_queue(limit: int = 100, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    return _state(ctx, limit)


@router.post("")
async def add(body: AddIn, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    changed = q.add(ctx.conn, valid_video_id(body.video_id), front=body.next)
    return {"added": changed, "count": q.count(ctx.conn)}


@router.put("/order")
async def order(body: OrderIn, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    q.reorder(ctx.conn, [valid_video_id(v) for v in body.video_ids])
    return _state(ctx)


@router.delete("/{video_id}")
async def remove(video_id: str, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    removed = q.remove(ctx.conn, valid_video_id(video_id))
    return {"removed": removed, "count": q.count(ctx.conn)}


@router.delete("")
async def clear(ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    return {"cleared": q.clear(ctx.conn), "count": 0}
