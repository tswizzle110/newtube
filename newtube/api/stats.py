"""Watch-time statistics. Local only: computed from your own watch sessions."""

from __future__ import annotations

import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Body, Depends, HTTPException

from ..app import Context
from ..db.queries import stats
from .deps import get_ctx

router = APIRouter(prefix="/api/stats")


def _zone(name: str) -> ZoneInfo:
    """The browser's time zone (e.g. 'Europe/London'), so days are YOUR days. Unknown names fall back to UTC."""
    try:
        return ZoneInfo(name) if name else ZoneInfo("UTC")
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return ZoneInfo("UTC")


@router.get("/watch")
async def watch(tz: str = "", ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    return stats.watch_stats(ctx.conn, _zone(tz[:64]))


@router.put("/since")
async def set_since(body: dict[str, Any] = Body(...), ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    """Count from this day (YYYY-MM-DD), or null to count from your first recorded watch."""
    value = body.get("since")
    if value is None or value == "":
        stats.save_since(ctx.conn, None)
    else:
        try:
            day = stats.parse_day(str(value))
        except ValueError:
            raise HTTPException(400, "since must be a date like 2026-11-02") from None
        if not dt.date(2000, 1, 1) <= day <= dt.date.today() + dt.timedelta(days=1):
            raise HTTPException(400, "that date is out of range")
        stats.save_since(ctx.conn, day)
    return {"since": stats.saved_since(ctx.conn).isoformat() if stats.saved_since(ctx.conn) else None}
