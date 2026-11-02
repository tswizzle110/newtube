-- Support for importing a Google Takeout.

-- "Do we already know this channel's uploads?" Until a channel has been polled successfully once (by RSS
-- or its channel page), videos found on it are just its backlog, not new uploads. This used to be inferred
-- from "any video row exists", which imported watch history would defeat: every subscribed channel would
-- look known, and its 15 newest uploads would flood the feed as "new".
ALTER TABLE channels ADD COLUMN feed_seeded INTEGER NOT NULL DEFAULT 0;
UPDATE channels SET feed_seeded = 1 WHERE EXISTS (SELECT 1 FROM videos v WHERE v.channel_id = channels.id);

-- Imported events (types starting with "import_") are de-duplicated, so importing the same export twice,
-- or a newer export that overlaps an older one, adds nothing twice. NULLs are normalised because a unique
-- index treats NULL as different from NULL.
CREATE UNIQUE INDEX events_import_unique
    ON events(type, ts, COALESCE(video_id, ''), COALESCE(payload, ''))
    WHERE substr(type, 1, 7) = 'import_';
