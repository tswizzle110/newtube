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

from ..recommend.baseline import DAY, interest_rows
from .embedder import normalize
from .store import dot, load_vectors


def taste_vector(conn: sqlite3.Connection, model: str, now: int, half_life_days: float = 180.0) -> list[float] | None:
    """Weighted mean of the embeddings of videos you showed interest in, or None when there is nothing to go on."""
    decay = math.log(2) / (half_life_days * DAY)
    weight: dict[str, float] = defaultdict(float)
    for vid, _channel, w, ts in interest_rows(conn, now):
        weight[vid] += w * math.exp(-decay * max(0, now - ts))
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
_near: dict[tuple, tuple[float, int, list[str]]] = {}      # (conn, model) -> (valid until, how many, ids)
CACHE_SECONDS = 600


def scorer(conn: sqlite3.Connection, model: str, now: int):
    """``ids -> {video_id: similarity to your taste}``, or None when there is no taste to compare with yet.
    The taste vector is reused for 10 minutes unless your history or the embeddings changed."""
    state = (conn.execute("SELECT count(*), COALESCE(max(id), 0) FROM events").fetchone()[:],
             conn.execute("SELECT count(*) FROM video_embeddings WHERE model=?", (model,)).fetchone()[0],
             conn.execute("SELECT count(*) FROM watch_sessions").fetchone()[0],
             conn.execute("SELECT count(*), COALESCE(sum(added_at), 0) FROM playlist_items").fetchone()[:])
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
    def score(ids):
        return {vid: dot(taste, vec) for vid, vec in load_vectors(conn, model, ids).items()}

    def nearest(k: int) -> list[str]:
        """The ``k`` embedded videos closest to your taste, from any channel. Going through every video's vector is slow on a
        big library, and your taste moves slowly, so the answer is reused for ten minutes however many events came in."""
        got = _near.get((id(conn), model))
        if got and got[0] > time.monotonic() and got[1] >= k:
            return got[2][:k]
        ranked = sorted(((dot(taste, vec), vid) for vid, vec in load_vectors(conn, model).items()), reverse=True)
        ids = [vid for _s, vid in ranked[:max(k, 120)]]
        _near[(id(conn), model)] = (time.monotonic() + CACHE_SECONDS, len(ids), ids)
        return ids[:k]

    score.nearest = nearest
    return score
