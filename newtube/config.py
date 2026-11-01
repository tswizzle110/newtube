"""Runtime configuration.

Order of precedence (highest first): environment variables (NEWTUBE_*),
``newtube.toml`` in the working directory (or the path in NEWTUBE_CONFIG),
then the defaults below. Everything the rest of the code needs is on the
single ``Settings`` object so there is one place to look.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any


@dataclass
class Settings:
    # Where the SQLite DB, caches and secrets live.
    data_dir: Path = Path("data")
    host: str = "0.0.0.0"
    port: int = 8080

    # Optional outbound proxy (e.g. "socks5://127.0.0.1:1080"). Applied to
    # every outbound request, including yt-dlp. Empty = direct.
    outbound_proxy: str = ""

    # Global rate limit for YouTube/SponsorBlock API-style requests.
    # Interactive requests (you clicked something) bypass the daily cap but
    # still respect the minimum spacing.
    min_request_interval: float = 0.8   # seconds between requests, before jitter
    request_jitter: float = 0.6         # extra random 0..jitter seconds
    background_daily_cap: int = 2000    # background-lane requests per day

    # Size-capped LRU cache for thumbnails/captions (bytes).
    cache_max_bytes: int = 3 * 1024**3

    # RSS poll interval per channel, seconds (jittered in the scheduler).
    rss_poll_interval: int = 3 * 3600

    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "newtube.db"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"


def _coerce(value: str, to: type) -> Any:
    if to is int:
        return int(value)
    if to is float:
        return float(value)
    if to is Path:
        return Path(value)
    return value


def load_settings(env: dict[str, str] | None = None) -> Settings:
    env = dict(os.environ) if env is None else env
    s = Settings()
    cfg_path = Path(env.get("NEWTUBE_CONFIG", "newtube.toml"))
    file_values: dict[str, Any] = {}
    if cfg_path.is_file():
        file_values = tomllib.loads(cfg_path.read_text())
    types = {f.name: f.type for f in fields(Settings)}
    simple = {"int": int, "float": float, "Path": Path, "str": str}
    for name, tname in types.items():
        if name == "extra":
            continue
        typ = simple.get(tname if isinstance(tname, str) else tname.__name__, str)
        if name in file_values:
            setattr(s, name, typ(file_values[name]))
        env_key = f"NEWTUBE_{name.upper()}"
        if env_key in env:
            setattr(s, name, _coerce(env[env_key], typ))
    return s
