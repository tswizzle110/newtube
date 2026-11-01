-- Phase 1 schema. All timestamps are unix seconds (UTC) stored as INTEGER.
-- Add new migrations as 002_*.sql, 003_*.sql ... never edit an applied one.

-- Key/value app settings (values are JSON text).
CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE channels (
    id            TEXT PRIMARY KEY,          -- UCxxxxxxxxxxxxxxxxxxxxxx
    title         TEXT NOT NULL,
    handle        TEXT,                      -- @name, if known
    avatar_url    TEXT,
    description   TEXT,
    rss_checked_at INTEGER,                  -- last successful RSS poll
    created_at    INTEGER NOT NULL
);

-- Current subscriptions. The subscribe/unsubscribe *history* is in `events`.
CREATE TABLE subscriptions (
    channel_id    TEXT PRIMARY KEY REFERENCES channels(id) ON DELETE CASCADE,
    subscribed_at INTEGER NOT NULL
);

-- Everything we know about a video. RSS creates rows with only the basics;
-- `detail_fetched_at` is NULL until the extractor has filled in the rest.
CREATE TABLE videos (
    id               TEXT PRIMARY KEY,       -- 11-char video id
    channel_id       TEXT REFERENCES channels(id) ON DELETE SET NULL,
    title            TEXT NOT NULL,
    description      TEXT,
    published_at     INTEGER,
    duration         INTEGER,                -- seconds
    view_count       INTEGER,
    category         TEXT,
    tags             TEXT,                   -- JSON array
    is_live          INTEGER NOT NULL DEFAULT 0,
    is_short         INTEGER NOT NULL DEFAULT 0,
    thumbnail_url    TEXT,
    first_seen_at    INTEGER NOT NULL,
    detail_fetched_at INTEGER
);
CREATE INDEX videos_channel_pub ON videos(channel_id, published_at DESC);
CREATE INDEX videos_pub ON videos(published_at DESC);

CREATE TABLE chapters (
    video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    idx      INTEGER NOT NULL,
    start    REAL NOT NULL,                  -- seconds
    title    TEXT NOT NULL,
    PRIMARY KEY (video_id, idx)
);

-- Generic TTL cache for extractor results (video info, format URLs, search...).
-- `key` looks like "video:<id>" or "search:<hash>".
CREATE TABLE meta_cache (
    key        TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,                -- JSON
    fetched_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE INDEX meta_cache_expiry ON meta_cache(expires_at);

-- One row per playback of a video (page open to page close).
-- `intervals` is a JSON list of [start, end] seconds actually played, so
-- watched fraction, rewatches and skips can all be derived later.
CREATE TABLE watch_sessions (
    id          TEXT PRIMARY KEY,            -- random id from the client
    video_id    TEXT NOT NULL,
    started_at  INTEGER NOT NULL,
    ended_at    INTEGER,
    source      TEXT,                        -- 'for_you','subs','search','related','direct',...
    intervals   TEXT NOT NULL DEFAULT '[]',
    max_position REAL NOT NULL DEFAULT 0,
    duration    REAL                         -- video length as the player saw it
);
CREATE INDEX watch_sessions_video ON watch_sessions(video_id);
CREATE INDEX watch_sessions_started ON watch_sessions(started_at);

-- Resume point + overall progress per video (latest state, not history).
CREATE TABLE video_progress (
    video_id   TEXT PRIMARY KEY,
    position   REAL NOT NULL,
    duration   REAL,
    completed  INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL
);

-- What a feed/list showed me. One row per card that became visible.
-- `request_id` groups all cards from one rendering of a feed.
CREATE TABLE impressions (
    id         INTEGER PRIMARY KEY,
    ts         INTEGER NOT NULL,
    video_id   TEXT NOT NULL,
    surface    TEXT NOT NULL,                -- 'for_you','subs','search','related','channel'
    position   INTEGER,                      -- rank within the list
    request_id TEXT,
    visible_ms INTEGER,
    clicked    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX impressions_video ON impressions(video_id);
CREATE INDEX impressions_ts ON impressions(ts);

-- Append-only log of every other signal. New signal types need no migration.
-- type examples: play, pause, seek, speed_change, comments_opened, like,
-- not_interested, subscribe, unsubscribe, search, search_click, sb_skip ...
CREATE TABLE events (
    id         INTEGER PRIMARY KEY,
    ts         INTEGER NOT NULL,
    type       TEXT NOT NULL,
    video_id   TEXT,
    channel_id TEXT,
    session_id TEXT,
    payload    TEXT                          -- JSON
);
CREATE INDEX events_type_ts ON events(type, ts);
CREATE INDEX events_video ON events(video_id);
