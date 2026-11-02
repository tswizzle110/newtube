-- Every outside host this server has connected to (see net/audit.py). Local only; the counters accumulate
-- across restarts. `kind`: 'connect' = a connection was opened, 'lookup' = a DNS name was resolved.
CREATE TABLE egress_log (
    host       TEXT NOT NULL,
    port       INTEGER NOT NULL,           -- 0 for lookups
    kind       TEXT NOT NULL CHECK (kind IN ('connect', 'lookup')),
    allowed    INTEGER NOT NULL,           -- 1 = the host is on the allowlist
    blocked    INTEGER NOT NULL DEFAULT 0, -- connections/lookups refused (enforce mode)
    count      INTEGER NOT NULL,
    first_seen INTEGER NOT NULL,
    last_seen  INTEGER NOT NULL,
    last_ip    TEXT,
    PRIMARY KEY (host, port, kind)
);
