-- "Related videos" as YouTube's Mix for a video returns them, kept locally. They feed the Up next column on the
-- watch page and are candidates for the recommender (videos near things you like, from channels you may not
-- subscribe to). `rank` is the position in the Mix (1 = closest).
CREATE TABLE related_videos (
    video_id   TEXT NOT NULL,
    related_id TEXT NOT NULL,
    rank       INTEGER NOT NULL,
    PRIMARY KEY (video_id, related_id)
);
CREATE INDEX related_by_related ON related_videos(related_id);

-- When a video's related list was last fetched (count 0 with a recent time = it failed or was empty; retried
-- later). Lets the page and the background job avoid asking again too soon.
CREATE TABLE related_fetched (
    video_id   TEXT PRIMARY KEY,
    fetched_at INTEGER NOT NULL,
    count      INTEGER NOT NULL
);
