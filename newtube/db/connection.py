"""SQLite connection helper and a tiny forward-only migration runner.

Migrations are the numbered ``.sql`` files in ``db/migrations``. Each is
applied once, in order, inside a transaction, and recorded in
``schema_migrations``. To change the schema, add a new file; never edit an
applied one.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from ..config import load_settings

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def connect(path: Path | str) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None -> we manage transactions explicitly.
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Apply pending migrations. Returns the names that were applied."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " name TEXT PRIMARY KEY, applied_at INTEGER NOT NULL)"
    )
    done = {r["name"] for r in conn.execute("SELECT name FROM schema_migrations")}
    applied: list[str] = []
    for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if f.name in done:
            continue
        conn.execute("BEGIN")
        try:
            # executescript would COMMIT first, so split statements ourselves.
            for stmt in _statements(f.read_text()):
                conn.execute(stmt)
            conn.execute(
                "INSERT INTO schema_migrations(name, applied_at) VALUES (?, ?)",
                (f.name, int(time.time())),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        applied.append(f.name)
    return applied


def _statements(sql: str) -> list[str]:
    """Split a migration file into statements (no semicolons inside strings, by convention)."""
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def migrate_cli() -> int:
    settings = load_settings()
    conn = connect(settings.db_path)
    applied = migrate(conn)
    print(f"database: {settings.db_path}")
    print("applied: " + (", ".join(applied) if applied else "nothing (up to date)"))
    return 0
