"""Safe snapshots of the database, taken while the app is running.

Uses SQLite's online backup, which copies a consistent view even while newtube is writing (a plain file copy of
a database in WAL mode can be torn). Each snapshot is written to a temporary name, checked (`integrity_check`,
and it must open), and only then renamed into place, so a half-written or damaged file is never mistaken for a
backup. Only files named like our own snapshots are ever listed or deleted by pruning.

The snapshots contain your whole history: they are created readable by you only.
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

NAME = re.compile(r"^newtube-\d{8}-\d{6}\.db$")


class BackupError(Exception):
    pass


@dataclass
class Backup:
    path: Path
    size: int
    events: int
    videos: int
    pruned: list[Path]


def list_backups(dest: Path) -> list[Path]:
    """Our snapshots in ``dest``, oldest first (the timestamp in the name sorts chronologically)."""
    if not dest.is_dir():
        return []
    return sorted(p for p in dest.iterdir() if p.is_file() and NAME.match(p.name))


def make_backup(conn: sqlite3.Connection, dest: Path, keep: int = 14, now: float | None = None) -> Backup:
    if keep < 1:
        raise BackupError("keep must be at least 1")
    dest.mkdir(parents=True, exist_ok=True)
    os.chmod(dest, 0o700)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(now if now is not None else time.time()))
    final = dest / f"newtube-{stamp}.db"
    tmp = dest / f".newtube-{stamp}.db.partial"
    tmp.unlink(missing_ok=True)
    try:
        out = sqlite3.connect(tmp)
        try:
            conn.backup(out)
            # The copy inherits WAL mode, which keeps side files (-wal/-shm) next to the database. A snapshot
            # must be ONE self-contained file, so switch it to the plain rollback journal (newtube turns WAL
            # back on by itself when it opens the file).
            if out.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                raise BackupError("could not make the copy a single self-contained file")
        finally:
            out.close()
        os.chmod(tmp, 0o600)
        events, videos = _verify(tmp)
        os.replace(tmp, final)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise BackupError(f"backup failed: {e}") from e
    old = list_backups(dest)[:-keep]
    for p in old:
        p.unlink()
    return Backup(final, final.stat().st_size, events, videos, old)


def _verify(path: Path) -> tuple[int, int]:
    """The copy must open read-only and pass SQLite's own check. Returns (events, videos) as a sanity figure."""
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        result = c.execute("PRAGMA integrity_check").fetchall()
        if result != [("ok",)]:
            raise BackupError(f"the copy failed its integrity check: {result[:3]}")
        return c.execute("SELECT count(*) FROM events").fetchone()[0], c.execute("SELECT count(*) FROM videos").fetchone()[0]
    finally:
        c.close()
