-- One vector per video describing what it is about (see newtube/ml). float32, little-endian, unit length.
-- `model` names the embedder that made it: vectors from different models are never compared, and changing
-- the model simply re-embeds everything. `created_at` lets a video be re-embedded when richer details
-- (description, tags) arrive from the backfill.
CREATE TABLE video_embeddings (
    video_id   TEXT PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
    model      TEXT NOT NULL,
    dim        INTEGER NOT NULL,
    vector     BLOB NOT NULL,
    created_at INTEGER NOT NULL
);
