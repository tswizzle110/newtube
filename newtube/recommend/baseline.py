"""The baseline recommender: a transparent, hand-weighted scoring formula.

It exists for two reasons: to be useful on day one, and to be the thing a learned model must beat in the
offline evaluation (phase 4). So it is deliberately simple and every number is explainable.

    score = sum over terms of  weight * feature      (each feature is between 0 and 1)

    channel_affinity   how much you watch this channel (history, likes, watch-later, with time decay)
    freshness          newer uploads score higher (half-life ``freshness_half_life_days``)
    subscribed         the channel is one you subscribe to
    saved              the video is in your Watch later
    resume             you started it in newtube and did not finish
    shown_unclicked    NEGATIVE: this was shown to you repeatedly and you never clicked it

Candidates are videos already in the database: recent uploads of subscribed channels, and your Watch-later
items. (Finding videos from channels you do not subscribe to needs related-video lookups: a later phase.)

Never recommended: videos you finished, videos you watched before (unless you stopped partway in newtube),
anything you marked "not interested", and channels you marked "don't recommend".

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

DAY = 86400


@dataclass(frozen=True)
class Weights:
    channel_affinity: float = 3.0
    freshness: float = 1.5
    subscribed: float = 1.0
    saved: float = 1.2
    resume: float = 0.8
    shown_unclicked: float = -1.5
    repeat_factor: float = 0.7               # per higher-ranked video from the same channel
    freshness_half_life_days: float = 7.0
    unknown_age_freshness: float = 0.2       # saved videos not yet filled in by the backfill
    affinity_half_life_days: float = 180.0
    window_days: int = 45                    # how far back subscribed channels' uploads are considered
    fatigue_days: int = 14
    fatigue_full_at: int = 5                 # this many unclicked impressions = the full penalty


# How much one event says about interest in the video's channel. Imported history only says "opened".
EVENT_INTEREST = {
    "import_watch": 0.35, "import_like": 1.0, "like": 1.0, "import_watch_later": 0.3, "import_playlist_add": 0.4,
}


@dataclass
class Reason:
    label: str
    points: float


@dataclass
class Recommendation:
    video: dict[str, Any]
    score: float
    reasons: list[Reason] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {**self.video, "why": {"score": round(self.score, 2),
                                      "reasons": [{"label": r.label, "points": round(r.points, 2)} for r in self.reasons]}}


# ---------------------------------------------------------------------------- signals

def channel_interest(conn: sqlite3.Connection, now: int, half_life_days: float) -> dict[str, float]:
    """Time-decayed sum of interest per channel, scaled so the most-watched channel is 1.0."""
    decay = math.log(2) / (half_life_days * DAY)
    total: dict[str, float] = defaultdict(float)

    def add(channel: str | None, weight: float, ts: int) -> None:
        if channel and weight > 0:
            total[channel] += weight * math.exp(-decay * max(0, now - ts))

    marks = ",".join("?" * len(EVENT_INTEREST))
    for r in conn.execute(
            f"SELECT e.type, e.ts, COALESCE(e.channel_id, v.channel_id) AS ch FROM events e"
            f" LEFT JOIN videos v ON v.id = e.video_id WHERE e.type IN ({marks})", tuple(EVENT_INTEREST)):
        add(r["ch"], EVENT_INTEREST[r["type"]], r["ts"])
    # Real playback in newtube says much more than "opened": weight by how much was actually watched.
    for r in conn.execute("SELECT w.started_at AS ts, w.max_position AS pos, w.duration AS dur, v.channel_id AS ch"
                          " FROM watch_sessions w JOIN videos v ON v.id = w.video_id"):
        fraction = min(1.0, r["pos"] / r["dur"]) if r["dur"] else 0.5
        add(r["ch"], 0.2 + 0.8 * fraction, r["ts"])
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


_CANDIDATES = """
SELECT v.id, v.title, v.channel_id, COALESCE(c.title, '') AS channel_title, v.published_at, v.duration,
       v.view_count, p.position AS resume_position, p.completed AS completed,
       (s.channel_id IS NOT NULL) AS subscribed,
       EXISTS (SELECT 1 FROM events e WHERE e.video_id = v.id AND e.type = 'import_watch_later') AS saved,
       (EXISTS (SELECT 1 FROM watch_sessions w WHERE w.video_id = v.id)
        OR EXISTS (SELECT 1 FROM events e WHERE e.video_id = v.id AND e.type = 'import_watch')) AS watched_before
FROM videos v
LEFT JOIN channels c ON c.id = v.channel_id
LEFT JOIN subscriptions s ON s.channel_id = v.channel_id
LEFT JOIN video_progress p ON p.video_id = v.id
WHERE v.title <> ''
  AND ((s.channel_id IS NOT NULL AND v.from_import = 0 AND v.published_at >= ?)
       OR EXISTS (SELECT 1 FROM events e WHERE e.video_id = v.id AND e.type = 'import_watch_later'))
"""


def _age_text(seconds: float) -> str:
    if seconds < 3600:
        return "less than an hour ago"
    if seconds < 2 * DAY:
        h = int(seconds // 3600)
        return f"{h} hour{'s' if h != 1 else ''} ago"
    return f"{int(seconds // DAY)} days ago"


# ---------------------------------------------------------------------------- ranking

def recommend(conn: sqlite3.Connection, *, limit: int = 60, now: int | None = None,
              weights: Weights = Weights()) -> list[Recommendation]:
    now = now if now is not None else int(time.time())
    w = weights
    interest = channel_interest(conn, now, w.affinity_half_life_days)
    unclicked = _fatigue(conn, now, w.fatigue_days)
    hidden_videos, hidden_channels = _blocked(conn)

    scored: list[Recommendation] = []
    for r in conn.execute(_CANDIDATES, (now - w.window_days * DAY,)):
        if r["id"] in hidden_videos or r["channel_id"] in hidden_channels or r["completed"]:
            continue
        resumable = bool(r["resume_position"]) and r["resume_position"] > 0
        if r["watched_before"] and not resumable:
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
        term(w.resume, 1.0 if resumable else 0.0, "You started this and did not finish")
        n = unclicked.get(r["id"], 0)
        if n:
            reasons.append(Reason(f"Shown {n} time{'s' if n != 1 else ''} without a click",
                                  w.shown_unclicked * min(1.0, n / w.fatigue_full_at)))
        video = {k: r[k] for k in ("id", "title", "channel_id", "channel_title", "published_at", "duration", "view_count",
                                   "resume_position")}
        video["completed"] = False
        video["seen"] = bool(r["watched_before"] or resumable)
        scored.append(Recommendation(video, sum(x.points for x in reasons), reasons))

    # Best first; ties broken by newer then id so the order is stable between requests.
    scored.sort(key=lambda x: (-x.score, -(x.video["published_at"] or 0), x.video["id"]))
    return _diversify(scored, w.repeat_factor, limit)


def _diversify(ranked: list[Recommendation], factor: float, limit: int) -> list[Recommendation]:
    """Greedy: repeatedly take the best remaining video, discounting channels already picked."""
    out: list[Recommendation] = []
    taken: dict[str | None, int] = defaultdict(int)
    pool = list(ranked)
    while pool and len(out) < limit:
        def adjusted(rec: Recommendation) -> float:
            return rec.score * factor ** taken[rec.video["channel_id"]] if rec.score > 0 else rec.score
        i = max(range(len(pool)), key=lambda j: (adjusted(pool[j]), -j))      # ties keep the earlier (better-ordered) one
        best = pool.pop(i)
        k = taken[best.video["channel_id"]]
        if k and best.score > 0:
            lost = best.score - adjusted(best)
            best.reasons.append(Reason(f"{k} higher video{'s' if k > 1 else ''} from {best.video['channel_title']} already shown",
                                       -lost))
            best.score = adjusted(best)
        taken[best.video["channel_id"]] += 1
        out.append(best)
    return out
