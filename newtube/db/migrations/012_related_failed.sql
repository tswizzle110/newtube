-- Tell "the lookup failed" apart from "YouTube really returned nothing". Both used to be count = 0, so the page could not
-- say which, and a failed lookup was treated as a result.
ALTER TABLE related_fetched ADD COLUMN failed INTEGER NOT NULL DEFAULT 0;
UPDATE related_fetched SET failed = 1 WHERE count = 0;      -- (everything recorded so far as empty was in fact a failure or unknown)
