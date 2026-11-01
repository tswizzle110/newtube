"""Small size-capped LRU cache on disk, for thumbnails/avatars (NOT for video).

Each entry is one file: first line is the content-type, then the bytes. Recency is
the file's mtime (touched on every hit). When the total exceeds ``max_bytes`` the
least recently used files are deleted.
"""

from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path


class DiskCache:
    def __init__(self, directory: Path, max_bytes: int) -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self._lock = threading.Lock()

    def _path(self, key: str) -> Path:
        return self.dir / hashlib.sha256(key.encode()).hexdigest()

    def get(self, key: str) -> tuple[bytes, str] | None:
        p = self._path(key)
        try:
            raw = p.read_bytes()
        except FileNotFoundError:
            return None
        os.utime(p)  # mark as recently used
        ctype, _, body = raw.partition(b"\n")
        return body, ctype.decode()

    def put(self, key: str, body: bytes, content_type: str) -> None:
        if len(body) > self.max_bytes:
            return
        with self._lock:
            tmp = self._path(key).with_suffix(".tmp")
            tmp.write_bytes(content_type.encode() + b"\n" + body)
            tmp.replace(self._path(key))
            self._evict()

    def total_bytes(self) -> int:
        return sum(f.stat().st_size for f in self.dir.iterdir() if f.is_file())

    def _evict(self) -> None:
        files = [(f.stat().st_mtime, f.stat().st_size, f) for f in self.dir.iterdir() if f.is_file()]
        total = sum(s for _, s, _ in files)
        for _, size, f in sorted(files, key=lambda x: x[0]):   # oldest first
            if total <= self.max_bytes:
                break
            f.unlink(missing_ok=True)
            total -= size
