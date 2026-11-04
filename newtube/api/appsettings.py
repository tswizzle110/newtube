"""The Settings page's editable settings (see settings_registry.py), traffic status, and a restart button."""

from __future__ import annotations

import asyncio
import os
import signal
import time
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from .. import settings_registry as reg
from ..app import Context
from ..jobs.backfill import used_last_day as backfill_used
from ..jobs.scheduler import FALLBACK_PER_HOUR
from ..settings_registry import Invalid
from .deps import get_ctx

router = APIRouter(prefix="/api")


def under_systemd() -> bool:
    """A service started by systemd is brought back by it after a restart. Run by hand, stopping would just stop it."""
    return bool(os.environ.get("INVOCATION_ID"))


def _status(ctx: Context) -> dict[str, Any]:
    gate = getattr(ctx.extractor, "gate", None)
    now = time.time()
    hour = ctx.conn.execute("SELECT count(*) FROM events WHERE type='channel_fallback' AND ts > ?", (int(now) - 3600,)).fetchone()[0]
    related_24h = ctx.conn.execute("SELECT count(*) FROM events WHERE type='related_fetch' AND ts > ?", (int(now) - 86400,)).fetchone()[0]
    return {
        "bot_check": {
            "active": bool(gate and gate.closed),
            "click_retry_seconds": gate.retry_after() if gate and gate.closed else 0,
            "background_paused_minutes": max(0, int((gate.background_until - now) / 60)) if gate else 0,
        },
        "usage": {
            "backfill_24h": backfill_used(ctx.conn), "backfill_cap": ctx.settings.backfill_daily_cap,
            "related_24h": related_24h, "related_cap": ctx.settings.related_daily_cap,
            "channel_lookups_hour": hour, "channel_lookups_cap": FALLBACK_PER_HOUR,
            "background_today": ctx.limiter.used_today(), "background_cap": ctx.limiter.daily_cap,
        },
    }


def _payload(ctx: Context) -> dict[str, Any]:
    stored = reg.load_overrides(ctx.conn)
    defaults = reg.snapshot(type(ctx.settings)())
    fields = []
    for f in reg.FIELDS:
        current = getattr(ctx.settings, f.name)
        running = ctx.started_with.get(f.name, current)
        saved = stored.get(f.name)
        fields.append({
            "name": f.name, "label": f.label, "help": f.help, "kind": f.kind, "group": f.group, "live": f.live,
            "min": f.minimum, "max": f.maximum, "choices": list(f.choices), "scale": f.scale, "unit": f.unit,
            "placeholder": f.placeholder, "value": current, "saved": saved, "default": defaults[f.name],
            "locked_by_env": reg.locked_by_env(f.name),
            "pending_restart": (not f.live) and saved is not None and saved != running,
        })
    return {
        "groups": [{"id": k, "title": v[0], "help": v[1]} for k, v in reg.GROUPS.items()],
        "fields": fields, "presets": reg.PRESETS, "defaults": defaults, "status": _status(ctx),
        "can_restart": under_systemd(),
        "pending_restart": any(x["pending_restart"] for x in fields),
    }


def _apply_live(ctx: Context, name: str, value: Any) -> None:
    """Make a changed setting take effect right now (only for settings marked live)."""
    setattr(ctx.settings, name, value)
    if name == "min_request_interval":
        ctx.limiter.min_interval = value
    elif name == "request_jitter":
        ctx.limiter.jitter = value
    elif name == "background_daily_cap":
        ctx.limiter.daily_cap = value


@router.get("/settings/app")
async def get_app_settings(ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    return _payload(ctx)


@router.put("/settings/app")
async def put_app_settings(body: dict[str, Any] = Body(...), ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    """``{"values": {name: value}}``. A value of null removes your saved choice (back to the file / default).
    Everything is checked first; if anything is wrong nothing at all is saved."""
    values = body.get("values")
    if not isinstance(values, dict):
        raise HTTPException(400, "send {\"values\": {...}}")
    clean: dict[str, Any] = {}
    for name, raw in values.items():
        if name not in reg.BY_NAME:
            raise HTTPException(400, detail={"message": f"{name} cannot be changed here", "field": name})
        if reg.locked_by_env(name):
            raise HTTPException(400, detail={"message": f"{reg.BY_NAME[name].label} is fixed by the NEWTUBE_{name.upper()} "
                                                        f"environment variable and cannot be changed here", "field": name})
        if raw is None:
            clean[name] = None
            continue
        try:
            clean[name] = reg.validate(name, raw)
        except Invalid as e:
            raise HTTPException(400, detail={"message": e.message, "field": e.name}) from None
    stored = reg.load_overrides(ctx.conn)
    for name, value in clean.items():
        if value is None:
            stored.pop(name, None)
        else:
            stored[name] = value
    reg.save_overrides(ctx.conn, stored)
    for name, value in clean.items():
        field = reg.BY_NAME[name]
        if field.live:
            _apply_live(ctx, name, reg.file_value(name) if value is None else value)
    return _payload(ctx)


@router.post("/system/restart")
async def restart(ctx: Context = Depends(get_ctx)) -> dict[str, bool]:
    """Stop the server; systemd (Restart=always) starts it again within seconds. Only offered under systemd."""
    if not under_systemd():
        raise HTTPException(400, "newtube was not started by systemd, so it would not come back by itself. "
                                 "Stop it and start it again by hand.")
    asyncio.get_running_loop().call_later(1.0, os.kill, os.getpid(), signal.SIGTERM)       # after this reply is sent
    return {"restarting": True}
