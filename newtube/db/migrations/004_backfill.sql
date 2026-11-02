-- Videos that only exist because a Takeout import named them. They have an id (and maybe a title) but no
-- details yet; the slow background backfill fills them in. They must NOT appear in the subscriptions feed
-- when their details arrive (the feed is "recent uploads"; an imported 2019 watch is not one), so they are
-- marked. A video later seen in a channel's real recent uploads is unmarked (see store_feed).
ALTER TABLE videos ADD COLUMN from_import INTEGER NOT NULL DEFAULT 0;

-- Only videos that did NOT succeed have a row; a successful fetch sets videos.detail_fetched_at.
--   retry: failed for a reason that may pass (network, YouTube hiccup): try again after next_try_at.
--   gone:  private / deleted / blocked, or failed too many times: never asked for again.
CREATE TABLE backfill_state (
    video_id    TEXT PRIMARY KEY,
    status      TEXT NOT NULL CHECK (status IN ('retry', 'gone')),
    attempts    INTEGER NOT NULL DEFAULT 0,
    next_try_at INTEGER NOT NULL DEFAULT 0,
    note        TEXT
);
