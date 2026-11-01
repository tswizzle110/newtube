"""Append-only signal log. Every user signal ends up here (see schema comments)."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any


def log_event(conn: sqlite3.Connection, type: str, *, video_id: str | None = None,
              channel_id: str | None = None, session_id: str | None = None,
              payload: dict[str, Any] | None = None, ts: int | None = None) -> None:
    conn.execute(
        "INSERT INTO events(ts, type, video_id, channel_id, session_id, payload)"
        " VALUES (?,?,?,?,?,?)",
        (ts if ts is not None else int(time.time()), type, video_id, channel_id, session_id,
         json.dumps(payload) if payload is not None else None),
    )
