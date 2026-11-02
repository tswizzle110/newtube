-- "Remove from history". Watches of a video up to `hidden_at` are ignored (not shown in History, not counted as
-- "watched before", not used by the recommender). Nothing is deleted from the event log, and because the
-- cut-off is a time, re-importing a newer Takeout cannot bring a removed item back, while watching it again
-- later (after `hidden_at`) counts normally.
CREATE TABLE history_hidden (
    video_id  TEXT PRIMARY KEY,
    hidden_at INTEGER NOT NULL
);
