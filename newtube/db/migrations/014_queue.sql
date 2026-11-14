-- The play queue: videos waiting to play next, in order. Like a playlist item there is no foreign key on video_id (a video
-- may be queued before its details are stored). `position` only orders; gaps and negative numbers are fine (Play next
-- puts a video in front of everything by taking the smallest position minus one).
CREATE TABLE queue_items (
    video_id TEXT PRIMARY KEY,
    position INTEGER NOT NULL,
    added_at INTEGER NOT NULL
);
CREATE INDEX queue_order ON queue_items(position);
