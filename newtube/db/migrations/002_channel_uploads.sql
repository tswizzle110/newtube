-- A channel's complete upload list, kept so sorting by Oldest/Popular doesn't re-read it every day.
-- ~115 bytes per video. `pos` orders newest-first (smaller = newer): a fresh full scan numbers 0,1,2...
-- and videos found later by a top-up get numbers below the current minimum, so they stay "newer".
CREATE TABLE channel_uploads (
    channel_id TEXT NOT NULL,
    video_id   TEXT NOT NULL,
    pos        INTEGER NOT NULL,
    title      TEXT NOT NULL,
    duration   INTEGER,
    view_count INTEGER,
    PRIMARY KEY (channel_id, video_id)
) WITHOUT ROWID;
CREATE INDEX channel_uploads_pos ON channel_uploads(channel_id, pos);

CREATE TABLE channel_scans (
    channel_id   TEXT PRIMARY KEY,
    total        INTEGER,                 -- the channel's video count as YouTube reported it (may be NULL)
    truncated    INTEGER NOT NULL,        -- 1 = the channel had more videos than we read
    scanned_at   INTEGER NOT NULL,        -- last FULL read (also refreshes view counts)
    topped_up_at INTEGER NOT NULL         -- last time we looked for new uploads
);
