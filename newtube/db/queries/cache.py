"""TTL cache for extractor results, stored in the ``meta_cache`` table."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any


def get(conn: sqlite3.Connection, key: str, now: int | None = None) -> Any | None:
    now = now if now is not None else int(time.time())
    row = conn.execute("SELECT payload FROM meta_cache WHERE key=? AND expires_at>?",
                       (key, now)).fetchone()
    return json.loads(row["payload"]) if row else None


def put(conn: sqlite3.Connection, key: str, payload: Any, ttl: int, now: int | None = None) -> None:
    now = now if now is not None else int(time.time())
    conn.execute(
        "INSERT INTO meta_cache(key,payload,fetched_at,expires_at) VALUES (?,?,?,?)"
        " ON CONFLICT(key) DO UPDATE SET payload=excluded.payload,"
        "  fetched_at=excluded.fetched_at, expires_at=excluded.expires_at",
        (key, json.dumps(payload), now, now + ttl),
    )


def purge_expired(conn: sqlite3.Connection, now: int | None = None) -> int:
    now = now if now is not None else int(time.time())
    return conn.execute("DELETE FROM meta_cache WHERE expires_at<=?", (now,)).rowcount


def delete(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM meta_cache WHERE key=?", (key,))
