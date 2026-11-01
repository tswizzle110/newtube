"""``newtube serve``."""

from __future__ import annotations

import logging

import uvicorn

from .app import create_app
from .config import load_settings


def serve() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    s = load_settings()
    uvicorn.run(create_app(), host=s.host, port=s.port, log_level="info")
    return 0
