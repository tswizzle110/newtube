"""The baseline recommender: a transparent, hand-weighted scoring formula.

It exists for two reasons: to be useful on day one, and to be the thing a learned model must beat in the
offline evaluation (phase 4). So it is deliberately simple and every number is explainable.

    score = sum over terms of  weight * feature      (each feature is between 0 and 1)

    channel_affinity   how much you watch this channel (history, likes, watch-later, with time decay)
    freshness          newer uploads score higher (half-life ``freshness_half_life_days``)
    subscribed         the channel is one you subscribe to
    saved              the video is in your Watch later
    related            YouTube lists it as related to videos you like (it may be from a channel you do not subscribe to)
    taste_similarity   how close its subject is to what you watch (needs embeddings; see ml/)
    shown_unclicked    NEGATIVE: this was shown to you repeatedly and you never clicked it

Candidates are videos already in the database: recent uploads of subscribed channels, your Watch-later items,
videos listed as related to the ones you like (see services/related.py), other videos from channels you watch but do
not subscribe to, and the videos whose subject is closest to your taste (embeddings). The last three are how videos
from channels you do not subscribe to get in; ``explore_share`` makes sure they get a fair share of the page.

Never recommended: videos you finished or watched before (one you stopped partway is in Continue watching on Home, not
here), anything you marked "not interested", and channels you marked "don't recommend".

After scoring, a light diversity pass multiplies a video's score by ``repeat_factor`` for every higher-ranked
video from the same channel, so one prolific channel cannot fill the page.

Each recommendation carries ``reasons``: the terms and the points each contributed, which the interface
shows as "why this".
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from ..db.queries.seen import WATCHED_EVENT_SQL, WATCHED_SESSION_SQL, not_hidden

DAY = 86400


@dataclass(frozen=True)
class Weights:
    channel_affinity: float = 2.0          # (was 3.0: a channel you watch a lot should not crowd out everything else)
    freshness: float = 1.5
    subscribed: float = 0.4                # (was 1.0: being subscribed is already most of the candidate list)
    saved: float = 1.2
    related: float = 2.5                   # near videos you like, via related lists (see services/related.py)
    related_seeds: int = 150               # how many of your most-liked videos' related lists are consulted
    related_full_at: float = 1.5           # this much support = the full related bonus
    taste_similarity: float = 3.0          # needs embeddings; see newtube/ml
    taste_floor: float = 0.15              # cosine similarity at or below this counts as 'not similar'
    taste_span: float = 0.6                # ...and floor + span counts as 'very similar'
    taste_candidates: int = 120            # the videos closest to your taste are candidates even from unknown channels
    shown_unclicked: float = -1.5
    repeat_factor: float = 0.7               # per higher-ranked video from the same channel
    freshness_half_life_days: float = 7.0
    unknown_age_freshness: float = 0.2       # saved videos not yet filled in by the backfill
    affinity_half_life_days: float = 180.0
    window_days: int = 45                    # how far back subscribed channels' uploads are considered
    watched_channel_days: int = 365          # ...and other videos from channels you watch but do not subscribe to
    watched_channels: int = 80               # how many such channels (the ones you watch most) are consulted
    explore_share: float = 0.4               # at least this share of the page is NOT subscribed channels, when there is enough
    fatigue_days: int = 14
    fatigue_full_at: int = 5                 # this many unclicked impressions = the full penalty


# How much one event says about interest in the video's channel. Imported history only says "opened".
# (Likes and Watch later are read from your lists instead: see ``interest_rows``.)
EVENT_INTEREST = {"import_watch": 0.35, "import_playlist_add": 0.4}
LIST_INTEREST = {"liked": 1.0, "watch_later": 0.3}
UNDATED_AGE_DAYS = 730           # a Takeout entry with no date counts as about two years old


@dataclass
class Reason:
    label: str
    points: float


@dataclass
class Recommendation:
    video: dict[str, Any]
    score: float
    reasons: list[Reason] = field(default_factory=list)
    explore: bool = False             # from a channel you do not subscribe to (and not saved): see Weights.explore_share

    def to_dict(self) -> dict[str, Any]:
        return {**self.video, "why": {"score": round(self.score, 2),
                                      "reasons": [{"label": r.label, "points": round(r.points, 2)} for r in self.reasons]}}


# ---------------------------------------------------------------------------- signals

def interest_rows(conn: sqlite3.Connection, now: int) -> list[tuple[str, str | None, float, int]]:
    """Everything that says you are interested in a video: (video_id, channel_id, weight, when).

    Imported history ("opened") counts a little; real playback counts by how much you watched; and a video
    that is on your Liked or Watch later list counts for as long as it stays there (take it off and it stops)."""
    rows: list[tuple[str, str | None, float, int]] = []
    marks = ",".join("?" * len(EVENT_INTEREST))
    for r in conn.execute(
            f"SELECT e.video_id, e.type, e.ts, COALESCE(e.channel_id, v.channel_id) AS ch FROM events e"
            f" LEFT JOIN videos v ON v.id = e.video_id WHERE e.video_id IS NOT NULL AND e.type IN ({marks})"
            f" AND (e.type <> 'import_watch' OR {not_hidden('e.video_id', 'e.ts')})",
            tuple(EVENT_INTEREST)):
        rows.append((r["video_id"], r["ch"], EVENT_INTEREST[r["type"]], r["ts"]))
    for r in conn.execute("SELECT w.video_id, w.started_at AS ts, w.max_position AS pos, w.duration AS dur,"
                          " v.channel_id AS ch FROM watch_sessions w LEFT JOIN videos v ON v.id = w.video_id"
                          f" WHERE {not_hidden('w.video_id', 'w.started_at')}"):
        fraction = min(1.0, r["pos"] / r["dur"]) if r["dur"] else 0.5
        rows.append((r["video_id"], r["ch"], 0.2 + 0.8 * fraction, r["ts"]))
    for r in conn.execute("SELECT i.video_id, i.added_at, p.kind, v.channel_id AS ch FROM playlist_items i"
                          " JOIN playlists p ON p.id = i.playlist_id LEFT JOIN videos v ON v.id = i.video_id"
                          " WHERE p.kind IN ('liked', 'watch_later')"):
        rows.append((r["video_id"], r["ch"], LIST_INTEREST[r["kind"]],
                     r["added_at"] or now - UNDATED_AGE_DAYS * DAY))
    return rows


def channel_interest(conn: sqlite3.Connection, now: int, half_life_days: float) -> dict[str, float]:
    """Time-decayed sum of interest per channel, scaled so the most-watched channel is 1.0."""
    decay = math.log(2) / (half_life_days * DAY)
    total: dict[str, float] = defaultdict(float)
    for _vid, channel, weight, ts in interest_rows(conn, now):
        if channel and weight > 0:
            total[channel] += weight * math.exp(-decay * max(0, now - ts))
    if not total:
        return {}
    top = max(total.values())
    return {ch: v / top for ch, v in total.items()}


def _fatigue(conn: sqlite3.Connection, now: int, days: int) -> dict[str, int]:
    return {r[0]: r[1] for r in conn.execute(
        "SELECT video_id, count(*) FROM impressions WHERE clicked = 0 AND ts > ? GROUP BY video_id", (now - days * DAY,))}


def _blocked(conn: sqlite3.Connection) -> tuple[set[str], set[str]]:
    """(videos marked not interested, channels marked don't-recommend)."""
    videos = {r[0] for r in conn.execute("SELECT video_id FROM events WHERE type='not_interested' AND video_id IS NOT NULL")}
    channels = set()
    for r in conn.execute("SELECT channel_id, video_id FROM events WHERE type='dont_recommend_channel'"):
        if r["channel_id"]:
            channels.add(r["channel_id"])
        elif r["video_id"]:
            row = conn.execute("SELECT channel_id FROM videos WHERE id=?", (r["video_id"],)).fetchone()
            if row and row[0]:
                channels.add(row[0])
    return videos, channels


_SELECT = """
SELECT v.id, v.title, v.channel_id, COALESCE(c.title, '') AS channel_title, v.published_at, v.duration,
       v.view_count, v.category AS category, p.position AS resume_position, p.completed AS completed,
       (s.channel_id IS NOT NULL) AS subscribed,
       EXISTS (SELECT 1 FROM playlist_items i JOIN playlists pl ON pl.id = i.playlist_id
               WHERE i.video_id = v.id AND pl.kind = 'watch_later') AS saved,
       (""" + WATCHED_SESSION_SQL + " OR " + WATCHED_EVENT_SQL + """) AS watched_before
FROM videos v
LEFT JOIN channels c ON c.id = v.channel_id
LEFT JOIN subscriptions s ON s.channel_id = v.channel_id
LEFT JOIN video_progress p ON p.video_id = v.id
WHERE v.title <> ''
"""

# Recent uploads of your subscriptions, and what you saved for later.
_CANDIDATES = _SELECT + """
  AND ((s.channel_id IS NOT NULL AND v.from_import = 0 AND v.published_at >= ?)
       OR EXISTS (SELECT 1 FROM playlist_items i JOIN playlists pl ON pl.id = i.playlist_id
                  WHERE i.video_id = v.id AND pl.kind = 'watch_later'))
"""


def related_support(conn: sqlite3.Connection, now: int, w: Weights) -> dict[str, float]:
    """Videos that YouTube lists as related to the videos you like most, as 0..1: stronger when the seed is one of
    your favourites and when the video sits near the top of its related list."""
    decay = math.log(2) / (w.affinity_half_life_days * DAY)
    seeds: dict[str, float] = defaultdict(float)
    for vid, _channel, weight, ts in interest_rows(conn, now):
        seeds[vid] += weight * math.exp(-decay * max(0, now - ts))
    top = sorted(seeds.items(), key=lambda kv: -kv[1])[: w.related_seeds]
    if not top:
        return {}
    best = top[0][1]
    support: dict[str, float] = defaultdict(float)
    for i in range(0, len(top), 400):
        chunk = top[i:i + 400]
        weight_of = dict(chunk)
        marks = ",".join("?" * len(chunk))
        for r in conn.execute(f"SELECT video_id, related_id, rank FROM related_videos WHERE video_id IN ({marks})",
                              [vid for vid, _ in chunk]):
            support[r["related_id"]] += (weight_of[r["video_id"]] / best) / (1 + (r["rank"] - 1) / 5)
    return {vid: min(1.0, s / w.related_full_at) for vid, s in support.items()}


def _rows_by_id(conn: sqlite3.Connection, ids: list[str]) -> list[sqlite3.Row]:
    out: list[sqlite3.Row] = []
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        out += conn.execute(_SELECT + f" AND v.id IN ({','.join('?' * len(chunk))})", chunk).fetchall()
    return out


def _watched_channel_rows(conn: sqlite3.Connection, interest: dict[str, float], since: int, limit: int) -> list[sqlite3.Row]:
    """Videos this server knows from channels you watch but do not subscribe to (they got here via searches, related
    lists and channel pages you opened), newest first within the channels you watch most."""
    top = [c for c, _ in sorted(interest.items(), key=lambda kv: -kv[1])[:limit]]
    out: list[sqlite3.Row] = []
    for i in range(0, len(top), 400):
        chunk = top[i:i + 400]
        out += conn.execute(_SELECT + f" AND s.channel_id IS NULL AND v.published_at >= ?"
                            f" AND v.channel_id IN ({','.join('?' * len(chunk))})", [since, *chunk]).fetchall()
    return out


def _age_text(seconds: float) -> str:
    if seconds < 3600:
        return "less than an hour ago"
    if seconds < 2 * DAY:
        h = int(seconds // 3600)
        return f"{h} hour{'s' if h != 1 else ''} ago"
    return f"{int(seconds // DAY)} days ago"


# ---------------------------------------------------------------------------- ranking

def recommend(conn: sqlite3.Connection, *, limit: int = 60, now: int | None = None,
              weights: Weights = Weights(), taste=None) -> list[Recommendation]:
    """``taste``: optional ``ids -> {video_id: cosine similarity to what you watch}`` (see ml/taste.py)."""
    now = now if now is not None else int(time.time())
    w = weights
    interest = channel_interest(conn, now, w.affinity_half_life_days)
    unclicked = _fatigue(conn, now, w.fatigue_days)
    hidden_videos, hidden_channels = _blocked(conn)

    rows = conn.execute(_CANDIDATES, (now - w.window_days * DAY,)).fetchall()
    support = related_support(conn, now, w)
    have = {r["id"] for r in rows}
    rows += _rows_by_id(conn, [vid for vid in support if vid not in have])    # videos near ones you like, from anywhere
    have |= {r["id"] for r in rows}
    rows += [r for r in _watched_channel_rows(conn, interest, now - w.watched_channel_days * DAY, w.watched_channels)
             if r["id"] not in have]                                           # other videos from channels you watch
    have |= {r["id"] for r in rows}
    nearest = getattr(taste, "nearest", None)
    if nearest:                                                                # videos about what you watch, from anywhere
        rows += _rows_by_id(conn, [vid for vid in nearest(w.taste_candidates) if vid not in have])
    similar = taste([r["id"] for r in rows]) if taste and rows else {}
    scored: list[Recommendation] = []
    for r in rows:
        if r["id"] in hidden_videos or r["channel_id"] in hidden_channels or r["completed"]:
            continue
        if r["watched_before"]:                          # (a video you STARTED waits in Continue watching, not in this list)
            continue
        reasons: list[Reason] = []

        def term(weight: float, feature: float, label: str) -> None:
            if feature > 0:
                reasons.append(Reason(label, weight * feature))

        affinity = interest.get(r["channel_id"], 0.0)
        term(w.channel_affinity, affinity,
             f"You watch {r['channel_title']} a lot" if affinity >= 0.5 else f"You have watched {r['channel_title']} before")
        if r["published_at"]:
            age = max(0, now - r["published_at"])
            term(w.freshness, 0.5 ** (age / (w.freshness_half_life_days * DAY)), f"Uploaded {_age_text(age)}")
        else:
            term(w.freshness, w.unknown_age_freshness, "Upload date not known yet")
        term(w.subscribed, 1.0 if r["subscribed"] else 0.0, "From a channel you subscribe to")
        term(w.saved, 1.0 if r["saved"] else 0.0, "In your Watch later")
        term(w.related, support.get(r["id"], 0.0), "Related to videos you have watched")
        if r["id"] in similar:
            term(w.taste_similarity, min(1.0, max(0.0, (similar[r["id"]] - w.taste_floor) / w.taste_span)),
                 "Similar to videos you have watched")
        n = unclicked.get(r["id"], 0)
        if n:
            reasons.append(Reason(f"Shown {n} time{'s' if n != 1 else ''} without a click",
                                  w.shown_unclicked * min(1.0, n / w.fatigue_full_at)))
        video = {k: r[k] for k in ("id", "title", "channel_id", "channel_title", "published_at", "duration", "view_count",
                                   "resume_position", "category")}
        video["completed"] = False
        video["seen"] = bool(r["watched_before"])
        scored.append(Recommendation(video, sum(x.points for x in reasons), reasons,
                                     explore=not r["subscribed"] and not r["saved"]))

    # Best first; ties broken by newer then id so the order is stable between requests.
    scored.sort(key=lambda x: (-x.score, -(x.video["published_at"] or 0), x.video["id"]))
    return _diversify(scored, w.repeat_factor, limit, w.explore_share)


def _diversify(ranked: list[Recommendation], factor: float, limit: int, explore_share: float = 0.0) -> list[Recommendation]:
    """Greedy: repeatedly take the best remaining video, discounting channels already picked. While fewer than
    ``explore_share`` of the page is from channels you do not subscribe to, the next pick is the best of those (as long
    as any are left), so a page of subscriptions' uploads cannot crowd everything else out."""
    out: list[Recommendation] = []
    taken: dict[str | None, int] = defaultdict(int)
    pool = list(ranked)
    explored = 0
    while pool and len(out) < limit:
        def adjusted(rec: Recommendation) -> float:
            return rec.score * factor ** taken[rec.video["channel_id"]] if rec.score > 0 else rec.score
        want_explore = explored < math.ceil(explore_share * (len(out) + 1)) and any(r.explore for r in pool)
        candidates = [j for j in range(len(pool)) if pool[j].explore] if want_explore else list(range(len(pool)))
        i = max(candidates, key=lambda j: (adjusted(pool[j]), -j))      # ties keep the earlier (better-ordered) one
        best = pool.pop(i)
        k = taken[best.video["channel_id"]]
        if k and best.score > 0:
            lost = best.score - adjusted(best)
            best.reasons.append(Reason(f"{k} higher video{'s' if k > 1 else ''} from {best.video['channel_title']} already shown",
                                       -lost))
            best.score = adjusted(best)
        taken[best.video["channel_id"]] += 1
        explored += best.explore
        out.append(best)
    return out
