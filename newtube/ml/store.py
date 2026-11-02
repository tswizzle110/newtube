"""Embedding storage, the text that gets embedded, and the work queue."""

from __future__ import annotations

import json
import sqlite3
import time
from array import array

from .embedder import Embedder

DESCRIPTION_CHARS = 400


def video_text(title: str, channel: str | None, category: str | None, tags: str | None, description: str | None) -> str:
    """What the model reads about a video: the title first (it matters most), then channel, category, tags and the
    start of the description."""
    parts = [title or "", channel or "", category or ""]
    try:
        parts.append(" ".join(json.loads(tags)[:12]) if tags else "")
    except (ValueError, TypeError):
        pass
    parts.append((description or "")[:DESCRIPTION_CHARS])
    return ". ".join(p.strip() for p in parts if p and p.strip())


def pack(vec: list[float]) -> bytes:
    return array("f", vec).tobytes()


def unpack(blob: bytes) -> list[float]:
    a = array("f")
    a.frombytes(blob)
    return a.tolist()


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


_PENDING = """
SELECT v.id, v.title, COALESCE(c.title, '') AS channel, v.category, v.tags, v.description
FROM videos v
LEFT JOIN channels c ON c.id = v.channel_id
LEFT JOIN video_embeddings e ON e.video_id = v.id
WHERE v.title <> ''
  AND (e.video_id IS NULL OR e.model <> ?
       OR (v.detail_fetched_at IS NOT NULL AND v.detail_fetched_at > e.created_at))
ORDER BY (SELECT count(*) FROM events ev WHERE ev.video_id = v.id) DESC, v.first_seen_at DESC
LIMIT ?
"""


def pending(conn: sqlite3.Connection, model: str, limit: int) -> list[sqlite3.Row]:
    return conn.execute(_PENDING, (model, limit)).fetchall()


def count_pending(conn: sqlite3.Connection, model: str) -> int:
    return conn.execute(f"SELECT count(*) FROM ({_PENDING.rsplit('LIMIT', 1)[0].rsplit('ORDER BY', 1)[0]})", (model,)).fetchone()[0]


def _texts(rows) -> list[str]:
    return [video_text(r["title"], r["channel"], r["category"], r["tags"], r["description"]) for r in rows]


def _save(conn: sqlite3.Connection, embedder: Embedder, rows, vectors, now: int | None) -> None:
    ts = now if now is not None else int(time.time())
    conn.executemany(
        "INSERT INTO video_embeddings(video_id, model, dim, vector, created_at) VALUES (?,?,?,?,?)"
        " ON CONFLICT(video_id) DO UPDATE SET model=excluded.model, dim=excluded.dim,"
        "  vector=excluded.vector, created_at=excluded.created_at",
        [(r["id"], embedder.name, len(v), pack(v), ts) for r, v in zip(rows, vectors)])


def embed_pending(conn: sqlite3.Connection, embedder: Embedder, limit: int = 64, now: int | None = None) -> int:
    """Embed up to ``limit`` videos that need it (those people interact with first). Returns how many."""
    rows = pending(conn, embedder.name, limit)
    if rows:
        _save(conn, embedder, rows, embedder.embed(_texts(rows)), now)
    return len(rows)


async def embed_pending_async(conn: sqlite3.Connection, embedder: Embedder, limit: int = 64) -> int:
    """Same, but the model runs in a worker thread so the server keeps answering while the GPU works. (Only the
    model is in the thread: the database is touched here, on the caller's thread.)"""
    import asyncio

    rows = pending(conn, embedder.name, limit)
    if rows:
        _save(conn, embedder, rows, await asyncio.to_thread(embedder.embed, _texts(rows)), None)
    return len(rows)


def load_vectors(conn: sqlite3.Connection, model: str, ids: list[str] | None = None) -> dict[str, list[float]]:
    if ids is None:
        rows = conn.execute("SELECT video_id, vector FROM video_embeddings WHERE model=?", (model,))
    else:
        rows = (r for i in range(0, len(ids), 500) for r in conn.execute(
            f"SELECT video_id, vector FROM video_embeddings WHERE model=? AND video_id IN ({','.join('?' * len(ids[i:i + 500]))})",
            (model, *ids[i:i + 500])))
    return {r[0]: unpack(r[1]) for r in rows}
