"""``newtube serve``."""

from __future__ import annotations

import logging

import uvicorn

from .app import build_context, create_app
from .config import load_settings
from .net import audit


def serve() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    settings = load_settings()
    audit.install_from_settings(settings)       # before anything can open a connection
    ctx = build_context(settings)
    if not ctx.auth.configured:
        print("No password set. Run `newtube passwd` first.")
        return 2
    s = ctx.settings
    uvicorn.run(create_app(ctx), host=s.host, port=s.port, log_level="info")
    return 0
