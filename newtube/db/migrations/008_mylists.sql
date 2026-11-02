-- Your own lists of videos: Liked videos, Watch later, and (later) playlists you make. Local only.
-- 'liked' and 'watch_later' exist exactly once each; any number of 'user' lists.
CREATE TABLE playlists (
    id         INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('liked', 'watch_later', 'user')),
    name       TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX playlists_system ON playlists(kind) WHERE kind <> 'user';

-- `position` orders a user playlist (you can reorder it). Liked / Watch later show newest-added first
-- (added_at 0 = a Takeout entry with no date, shown last). No foreign key on video_id: a list may name a
-- video we have not stored details for yet.
CREATE TABLE playlist_items (
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    video_id    TEXT NOT NULL,
    added_at    INTEGER NOT NULL,
    position    INTEGER NOT NULL,
    PRIMARY KEY (playlist_id, video_id)
);
CREATE INDEX playlist_items_order ON playlist_items(playlist_id, position);
CREATE INDEX playlist_items_video ON playlist_items(video_id);

INSERT INTO playlists(kind, name, created_at, updated_at) VALUES
    ('liked', 'Liked videos', strftime('%s', 'now'), strftime('%s', 'now')),
    ('watch_later', 'Watch later', strftime('%s', 'now'), strftime('%s', 'now'));

-- Anything imported from a Takeout before this existed.
INSERT OR IGNORE INTO playlist_items(playlist_id, video_id, added_at, position)
SELECT (SELECT id FROM playlists WHERE kind = 'liked'), video_id, MIN(ts), ROW_NUMBER() OVER (ORDER BY MIN(ts))
  FROM events WHERE type = 'import_like' AND video_id IS NOT NULL GROUP BY video_id;
INSERT OR IGNORE INTO playlist_items(playlist_id, video_id, added_at, position)
SELECT (SELECT id FROM playlists WHERE kind = 'watch_later'), video_id, MIN(ts), ROW_NUMBER() OVER (ORDER BY MIN(ts))
  FROM events WHERE type = 'import_watch_later' AND video_id IS NOT NULL GROUP BY video_id;
