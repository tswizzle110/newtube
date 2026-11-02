"""One definition of "you have watched this before", used by every list that shows video cards.

True when ANY of these exist: an imported Takeout watch, a watch session recorded by newtube, or saved
progress. It is deliberately separate from ``completed`` (finished the video), which is only ever set by
real playback in newtube: imported history says a video was opened, not how much of it was seen.

Watches you removed from History do not count (see ``not_hidden``).

Needs the query to alias the videos table as ``v`` and ``video_progress`` as ``p`` (LEFT JOINed)."""


def not_hidden(video_col: str, ts_col: str) -> str:
    """SQL: this watch (of ``video_col`` at ``ts_col``) was not removed from History."""
    return f"NOT EXISTS (SELECT 1 FROM history_hidden hh WHERE hh.video_id = {video_col} AND hh.hidden_at >= {ts_col})"


WATCHED_EVENT_SQL = ("EXISTS (SELECT 1 FROM events e WHERE e.video_id = v.id AND e.type = 'import_watch' AND "
                     + not_hidden("e.video_id", "e.ts") + ")")
WATCHED_SESSION_SQL = ("EXISTS (SELECT 1 FROM watch_sessions w WHERE w.video_id = v.id AND "
                       + not_hidden("w.video_id", "w.started_at") + ")")

SEEN_SQL = f"(p.video_id IS NOT NULL OR {WATCHED_SESSION_SQL} OR {WATCHED_EVENT_SQL})"
