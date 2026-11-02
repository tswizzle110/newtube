"""One definition of "you have watched this before", used by every list that shows video cards.

True when ANY of these exist: an imported Takeout watch, a watch session recorded by newtube, or saved
progress. It is deliberately separate from ``completed`` (finished the video), which is only ever set by
real playback in newtube: imported history says a video was opened, not how much of it was seen.

Needs the query to alias the videos table as ``v`` and ``video_progress`` as ``p`` (LEFT JOINed)."""

SEEN_SQL = (
    "(p.video_id IS NOT NULL"
    " OR EXISTS (SELECT 1 FROM watch_sessions w WHERE w.video_id = v.id)"
    " OR EXISTS (SELECT 1 FROM events e WHERE e.video_id = v.id AND e.type = 'import_watch'))"
)
