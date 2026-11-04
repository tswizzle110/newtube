-- Batches of events the browser has already delivered. The browser keeps unsent batches (laptop asleep, wifi switched)
-- and sends them again until the server says it has them; if the answer was lost on the way, the same batch arrives
-- twice, and this table is how the second copy is recognised and ignored. Old ids are purged after two weeks.
CREATE TABLE event_batches (
    id          TEXT PRIMARY KEY,
    received_at INTEGER NOT NULL
);
CREATE INDEX event_batches_received ON event_batches(received_at);
