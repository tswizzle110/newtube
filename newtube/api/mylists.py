"""Your own lists: Liked videos, Watch later, and playlists you make. Local only; nothing is sent to YouTube."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..app import Context
from ..db.queries import mylists as ml
from .deps import get_ctx, valid_video_id

router = APIRouter(prefix="/api/mylists")


class ItemIn(BaseModel):
    video_id: str


class NameIn(BaseModel):
    name: str


class OrderIn(BaseModel):
    video_ids: list[str] = Field(max_length=5000)


def _list(ctx: Context, ref: str):
    try:
        return ml.resolve(ctx.conn, ref)
    except ml.NoSuchList:
        raise HTTPException(404, "no such list") from None


@router.get("")
async def all_lists(ctx: Context = Depends(get_ctx)) -> list[dict[str, Any]]:
    return ml.list_all(ctx.conn)


def _guard(fn, *args):
    """Turn the data layer's complaints into proper HTTP answers."""
    try:
        return fn(*args)
    except ml.NameTaken:
        raise HTTPException(409, "you already have a list with that name") from None
    except ml.NotYours:
        raise HTTPException(400, "Liked videos and Watch later cannot be renamed, deleted or reordered") from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post("")
async def create_list(body: NameIn, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    p = _guard(ml.create, ctx.conn, body.name)
    return {"id": p["id"], "kind": "user", "name": p["name"], "count": 0}


@router.patch("/{ref}")
async def rename_list(ref: str, body: NameIn, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    p = _list(ctx, ref)
    _guard(ml.rename, ctx.conn, p, body.name)
    return {"id": p["id"], "name": " ".join(body.name.split())}


@router.delete("/{ref}")
async def delete_list(ref: str, ctx: Context = Depends(get_ctx)) -> dict[str, bool]:
    _guard(ml.delete, ctx.conn, _list(ctx, ref))
    return {"deleted": True}


@router.put("/{ref}/order")
async def reorder_list(ref: str, body: OrderIn, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    return {"video_ids": _guard(ml.reorder, ctx.conn, _list(ctx, ref), [valid_video_id(v) for v in body.video_ids])}


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
