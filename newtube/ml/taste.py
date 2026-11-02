"""Your taste as one vector: the weighted average of the videos you have watched, liked or saved.

A candidate's similarity to it (cosine, because vectors are unit length) says "this is about the kind of thing you
watch". It reuses the same event weights and time decay as the channel-interest signal, so the two agree about
what counts as interest.
"""

from __future__ import annotations

import math
import sqlite3
import time
from collections import defaultdict

from ..recommend.baseline import DAY, EVENT_INTEREST
from .embedder import normalize
from .store import dot, load_vectors


def taste_vector(conn: sqlite3.Connection, model: str, now: int, half_life_days: float = 180.0) -> list[float] | None:
    """Weighted mean of the embeddings of videos you showed interest in, or None when there is nothing to go on."""
    decay = math.log(2) / (half_life_days * DAY)
    weight: dict[str, float] = defaultdict(float)
    marks = ",".join("?" * len(EVENT_INTEREST))
    for r in conn.execute(f"SELECT video_id, type, ts FROM events WHERE video_id IS NOT NULL AND type IN ({marks})",
                          tuple(EVENT_INTEREST)):
        weight[r["video_id"]] += EVENT_INTEREST[r["type"]] * math.exp(-decay * max(0, now - r["ts"]))
    for r in conn.execute("SELECT video_id, started_at AS ts, max_position AS pos, duration AS dur FROM watch_sessions"):
        fraction = min(1.0, r["pos"] / r["dur"]) if r["dur"] else 0.5
        weight[r["video_id"]] += (0.2 + 0.8 * fraction) * math.exp(-decay * max(0, now - r["ts"]))
    if not weight:
        return None
    vectors = load_vectors(conn, model, list(weight))
    if not vectors:
        return None
    dim = len(next(iter(vectors.values())))
    total = [0.0] * dim
    for vid, vec in vectors.items():
        w = weight[vid]
        for i, x in enumerate(vec):
            total[i] += w * x
    return normalize(total)


_cache: dict[tuple, tuple[float, list[float] | None]] = {}
CACHE_SECONDS = 600


def scorer(conn: sqlite3.Connection, model: str, now: int):
    """``ids -> {video_id: similarity to your taste}``, or None when there is no taste to compare with yet.
    The taste vector is reused for 10 minutes unless your history or the embeddings changed."""
    state = (conn.execute("SELECT count(*), COALESCE(max(id), 0) FROM events").fetchone()[:],
             conn.execute("SELECT count(*) FROM video_embeddings WHERE model=?", (model,)).fetchone()[0],
             conn.execute("SELECT count(*) FROM watch_sessions").fetchone()[0])
    key = (id(conn), model, state)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        taste = hit[1]
    else:
        taste = taste_vector(conn, model, now)
        _cache.clear()
        _cache[key] = (time.monotonic(), taste)
    if taste is None:
        return None
    return lambda ids: {vid: dot(taste, vec) for vid, vec in load_vectors(conn, model, ids).items()}
