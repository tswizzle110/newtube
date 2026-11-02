"""Open a Google Takeout: .zip files (read in place, never extracted), folders, or single files."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, TextIO


@dataclass
class Member:
    name: str                              # path inside its container, with "/" separators
    container: str                         # the zip / folder it came from
    size: int | None
    _open: Callable[[], BinaryIO]

    @property
    def suffix(self) -> str:
        return PurePosixPath(self.name).suffix.lower()

    @property
    def stem(self) -> str:
        return PurePosixPath(self.name).stem

    @contextmanager
    def text(self) -> Iterator[TextIO]:
        """UTF-8 text (a BOM is skipped); csv wants newline='' so embedded line breaks survive."""
        raw = self._open()
        try:
            with io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="") as t:
                yield t
        finally:
            raw.close()


def open_takeout(paths: list[str | Path]) -> Iterator[Member]:
    """Every file in the given zips/folders/files. Archive members are only ever READ (a hostile
    name like '../../x' is just a label here; nothing is written to disk)."""
    for p in map(Path, paths):
        if p.is_dir():
            for f in sorted(x for x in p.rglob("*") if x.is_file()):
                yield Member(f.relative_to(p).as_posix(), str(p), f.stat().st_size, lambda f=f: open(f, "rb"))
        elif zipfile.is_zipfile(p):
            zf = zipfile.ZipFile(p)
            for info in zf.infolist():
                if not info.is_dir():
                    yield Member(info.filename, str(p), info.file_size, lambda info=info, zf=zf: zf.open(info))
        elif p.is_file():
            yield Member(p.name, str(p.parent), p.stat().st_size, lambda p=p: open(p, "rb"))
        else:
            raise FileNotFoundError(f"{p}: no such file or folder")
