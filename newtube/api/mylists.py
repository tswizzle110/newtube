"""Your own lists: Liked videos, Watch later, and playlists you make. Local only; nothing is sent to YouTube."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..app import Context
from ..db.queries import mylists as ml
from .deps import get_ctx, valid_video_id

router = APIRouter(prefix="/api/mylists")


class ItemIn(BaseModel):
    video_id: str


def _list(ctx: Context, ref: str):
    try:
        return ml.resolve(ctx.conn, ref)
    except ml.NoSuchList:
        raise HTTPException(404, "no such list") from None


@router.get("")
async def all_lists(ctx: Context = Depends(get_ctx)) -> list[dict[str, Any]]:
    return ml.list_all(ctx.conn)


@router.get("/for-video/{video_id}")
async def for_video(video_id: str, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    return ml.membership(ctx.conn, valid_video_id(video_id))


@router.get("/{ref}")
async def one_list(ref: str, limit: int = 60, offset: int = 0, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    p = _list(ctx, ref)
    count = ctx.conn.execute("SELECT count(*) FROM playlist_items WHERE playlist_id=?", (p["id"],)).fetchone()[0]
    return {"list": {"id": p["id"], "kind": p["kind"], "name": p["name"], "count": count},
            "videos": ml.items(ctx.conn, p, min(max(limit, 1), 200), max(offset, 0))}


@router.post("/{ref}/items")
async def add_item(ref: str, body: ItemIn, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    p = _list(ctx, ref)
    vid = valid_video_id(body.video_id)
    return {"added": ml.add(ctx.conn, p, vid), **ml.membership(ctx.conn, vid)}


@router.delete("/{ref}/items/{video_id}")
async def remove_item(ref: str, video_id: str, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    p = _list(ctx, ref)
    vid = valid_video_id(video_id)
    return {"removed": ml.remove(ctx.conn, p, vid), **ml.membership(ctx.conn, vid)}
