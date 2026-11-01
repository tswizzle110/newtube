"""``newtube selftest``: is extraction working from this machine, right now?

Each check is independent: one failure never hides the others. Exit status is
non-zero if any check fails, so this can also run from cron/systemd timers.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import json
import shutil
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .config import Settings, load_settings
from .extract.base import Extractor, VideoInfo
from .net.http import HostNotAllowed, OutboundClient, check_url
from .net.ratelimit import RateLimiter

# Long-lived, boring test targets. Change here if one ever disappears.
TEST_VIDEO = "jNQXAC9IVRw"                       # "Me at the zoo", the first YouTube video
TEST_CHANNEL_HANDLE = "@YouTube"
TEST_CHANNEL_ID = "UCBR8-60-B28hp2BmDPdntcQ"     # official YouTube channel (RSS test)


@dataclass
class Result:
    name: str
    ok: bool
    seconds: float
    detail: str


def redact_info(obj: Any) -> Any:
    """Strip query strings from URLs so a dumped info dict has no signed/IP-bound tokens."""
    if isinstance(obj, dict):
        return {k: redact_info(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_info(v) for v in obj]
    if isinstance(obj, str) and obj.startswith("https://") and "?" in obj:
        return obj.split("?", 1)[0] + "?REDACTED"
    return obj


def sponsorblock_prefix(video_id: str) -> str:
    """SponsorBlock lets us ask by the first 4 hex chars of sha256(video_id), so the
    server never learns which video we mean."""
    return hashlib.sha256(video_id.encode()).hexdigest()[:4]


class SelfTest:
    def __init__(self, settings: Settings, extractor: Extractor, http: OutboundClient,
                 dump_dir: Path | None = None) -> None:
        self.settings, self.extractor, self.http, self.dump_dir = settings, extractor, http, dump_dir
        self.results: list[Result] = []
        self.info: VideoInfo | None = None

    async def _check(self, name: str, fn: Callable[[], Awaitable[str]]) -> None:
        t0 = time.monotonic()
        try:
            detail, ok = await fn(), True
        except Exception as e:  # noqa: BLE001 - a selftest must report, not crash
            detail, ok = f"{type(e).__name__}: {e}", False
        self.results.append(Result(name, ok, time.monotonic() - t0, detail))

    # ---- individual checks -------------------------------------------------

    async def environment(self) -> str:
        s = self.settings
        rt = shutil.which(s.js_runtime_path or s.js_runtime)
        if not rt:
            raise RuntimeError(f"JS runtime '{s.js_runtime}' not found on PATH "
                               "(install deno: https://deno.com)")
        ejs = importlib.metadata.version("yt-dlp-ejs")
        ytdlp = importlib.metadata.version("yt-dlp")
        ff = shutil.which("ffmpeg")
        return f"yt-dlp {ytdlp}, yt-dlp-ejs {ejs}, {s.js_runtime} at {rt}, " \
               f"ffmpeg {'at ' + ff if ff else 'MISSING (only needed for remux fallback)'}"

    async def allowlist(self) -> str:
        check_url("https://www.youtube.com/")
        try:
            check_url("https://example.com/")
        except HostNotAllowed:
            return "youtube.com allowed, example.com refused"
        raise RuntimeError("example.com was NOT refused")

    async def rss(self) -> str:
        r = await self.http.get(
            f"https://www.youtube.com/feeds/videos.xml?channel_id={TEST_CHANNEL_ID}")
        r.raise_for_status()
        n = r.text.count("<entry>")
        if n == 0:
            raise RuntimeError("feed had no <entry> elements")
        return f"{n} entries"

    async def video_info(self) -> str:
        if self.dump_dir and hasattr(self.extractor, "raw_video_info"):
            raw = await self.extractor.raw_video_info(TEST_VIDEO)  # type: ignore[attr-defined]
            self.dump_dir.mkdir(parents=True, exist_ok=True)
            path = self.dump_dir / f"video_{TEST_VIDEO}.json"
            path.write_text(json.dumps(redact_info(raw), indent=1))
            from .extract.ytdlp import parse_video_info
            self.info = parse_video_info(raw)
            dumped = f", dumped to {path}"
        else:
            self.info, dumped = await self.extractor.video_info(TEST_VIDEO), ""
        v = self.info
        vids = [f for f in v.formats if f.has_video and not f.has_audio]
        auds = [f for f in v.formats if f.has_audio and not f.has_video]
        muxed = [f for f in v.formats if f.is_muxed]
        if not v.formats:
            raise RuntimeError("no playable formats returned")
        top = max((f.height or 0 for f in v.formats), default=0)
        return (f"'{v.title}' {v.duration}s; {len(vids)} video-only, {len(auds)} audio-only, "
                f"{len(muxed)} muxed; best {top}p; {len(v.captions)} caption tracks{dumped}")

    async def _range(self, url: str) -> str:
        async with self.http.stream(url, headers={"Range": "bytes=0-1023"}) as r:
            if r.status_code == 403:
                raise RuntimeError("403 from googlevideo (URL rejected: may need a PO token "
                                   "or a yt-dlp upgrade)")
            body = await r.aread()
            if r.status_code != 206 or len(body) != 1024:
                raise RuntimeError(f"expected 206 + 1024 bytes, got {r.status_code} + {len(body)}")
            return f"206, {urlsplit(url).hostname}, total {r.headers.get('content-range', '?')}"

    async def stream_range(self) -> str:
        if not self.info:
            raise RuntimeError("skipped: video_info failed")
        vids = sorted((f for f in self.info.formats if f.has_video and not f.has_audio),
                      key=lambda f: f.height or 0)
        auds = sorted((f for f in self.info.formats if f.has_audio and not f.has_video),
                      key=lambda f: f.tbr or 0)
        muxed = [f for f in self.info.formats if f.is_muxed]
        out = []
        if vids:
            out.append(f"video {vids[-1].height}p: " + await self._range(vids[-1].url))
        if auds:
            out.append("audio: " + await self._range(auds[-1].url))
        if muxed:
            out.append(f"muxed {muxed[-1].height}p: " + await self._range(muxed[-1].url))
        if not out:
            raise RuntimeError("no formats to test")
        return "; ".join(out)

    async def thumbnail(self) -> str:
        r = await self.http.get(f"https://i.ytimg.com/vi/{TEST_VIDEO}/hqdefault.jpg")
        r.raise_for_status()
        return f"{len(r.content)} bytes, {r.headers.get('content-type')}"

    async def channel(self) -> str:
        c = await self.extractor.resolve_channel(TEST_CHANNEL_HANDLE)
        return f"{TEST_CHANNEL_HANDLE} -> {c.id} '{c.title}', avatar {'yes' if c.avatar_url else 'no'}"

    async def sponsorblock(self) -> str:
        p = sponsorblock_prefix(TEST_VIDEO)
        r = await self.http.get(f"https://sponsor.ajay.app/api/skipSegments/{p}")
        if r.status_code not in (200, 404):
            raise RuntimeError(f"unexpected status {r.status_code}")
        return f"hash-prefix lookup /{p} -> HTTP {r.status_code}"

    async def run(self) -> list[Result]:
        for name, fn in [
            ("environment", self.environment), ("allowlist", self.allowlist),
            ("rss", self.rss), ("video_info", self.video_info),
            ("stream_range", self.stream_range), ("thumbnail", self.thumbnail),
            ("channel", self.channel), ("sponsorblock", self.sponsorblock),
        ]:
            await self._check(name, fn)
        return self.results


def format_results(results: list[Result]) -> str:
    lines = []
    for r in results:
        lines.append(f"[{' OK ' if r.ok else 'FAIL'}] {r.name:<13}{r.seconds:5.1f}s  {r.detail}")
    bad = sum(not r.ok for r in results)
    lines.append("")
    lines.append("all checks passed" if not bad else f"{bad} check(s) FAILED")
    return "\n".join(lines)


def run(dump_dir: str | None = None) -> int:
    from .extract.ytdlp import YtDlpExtractor

    settings = load_settings()
    limiter = RateLimiter(settings.min_request_interval, settings.request_jitter,
                          settings.background_daily_cap)

    async def main() -> list[Result]:
        http = OutboundClient(settings, limiter)
        try:
            st = SelfTest(settings, YtDlpExtractor(settings, limiter), http,
                          Path(dump_dir) if dump_dir else None)
            return await st.run()
        finally:
            await http.aclose()

    print(f"newtube selftest (python {sys.version.split()[0]}, proxy: "
          f"{settings.outbound_proxy or 'none'})\n")
    results = asyncio.run(main())
    print(format_results(results))
    return 0 if all(r.ok for r in results) else 1
