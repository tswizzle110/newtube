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
import re
from urllib.parse import parse_qs, urlsplit

from .config import Settings, load_settings
from .extract.base import ChannelSummary, Extractor, PlaylistSummary, VideoInfo, VideoSummary
from .extract.rss import feed_url, parse_feed
from .net.http import HostNotAllowed, OutboundClient, check_url
from .net.ratelimit import RateLimiter

# Long-lived, boring test targets. Change here if one ever disappears.
TEST_VIDEO = "jNQXAC9IVRw"                       # "Me at the zoo", the first YouTube video
TEST_VIDEO_HD = "aqz-KE-bpKQ"                    # Big Buck Bunny in 4K (Blender): has VP9/AV1 up to 2160p
TEST_VIDEO_2 = "dQw4w9WgXcQ"                     # a second video, to time a "warm" extraction
TEST_CHANNEL_HANDLE = "@YouTube"
TEST_CHANNEL_ID = "UCBR8-60-B28hp2BmDPdntcQ"     # official YouTube channel (RSS test)
TEST_QUERIES = {"video": "me at the zoo", "channel": "youtube", "playlist": "crash course"}


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
        self.playlist_id: str | None = None
        self.first_info_seconds = 0.0
        self.samples: list[str] = []

    async def _check(self, name: str, fn: Callable[[], Awaitable[str]]) -> None:
        t0 = time.monotonic()
        try:
            detail, ok = await fn(), True
        except Exception as e:  # noqa: BLE001 - a selftest must report, not crash
            detail, ok = f"{type(e).__name__}: {e}", False
        self.results.append(Result(name, ok, time.monotonic() - t0, detail))

    def _dump(self, name: str, raw: object) -> None:
        """With --dump: save redacted raw extractor output, plus the first entries as short text
        samples (samples.txt) that are easy to paste into a bug report."""
        if not self.dump_dir:
            return
        self.dump_dir.mkdir(parents=True, exist_ok=True)
        red = redact_info(raw)
        (self.dump_dir / f"{name}.json").write_text(json.dumps(red, indent=1))
        entries = (red.get("entries") or red.get("comments")) if isinstance(red, dict) else None
        for e in (entries or [])[:3]:
            self.samples.append(f"## {name}\n{json.dumps(e)[:900]}\n")
        (self.dump_dir / "samples.txt").write_text("\n".join(self.samples))

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
        r = await self.http.get(feed_url(TEST_CHANNEL_ID))
        r.raise_for_status()
        if self.dump_dir:
            self.dump_dir.mkdir(parents=True, exist_ok=True)
            (self.dump_dir / "rss.xml").write_text(r.text)
        feed = parse_feed(r.text)           # the same parser the app uses
        if not feed.entries:
            raise RuntimeError("feed parsed but has no entries")
        if feed.channel_id != TEST_CHANNEL_ID:
            raise RuntimeError(f"channel id {feed.channel_id!r} != requested {TEST_CHANNEL_ID!r}")
        e = feed.entries[0]
        return f"'{feed.channel_title}': {len(feed.entries)} entries, newest '{e.title[:40]}'"

    async def video_info(self) -> str:
        t0 = time.monotonic()
        try:
            return await self._video_info()
        finally:
            self.first_info_seconds = time.monotonic() - t0

    async def _video_info(self) -> str:
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

    async def video_info_warm(self) -> str:
        """A second extraction on the SAME extractor: shows what keeping yt-dlp's player cache warm saves."""
        t0 = time.monotonic()
        v = await self.extractor.video_info(TEST_VIDEO_2)
        took = time.monotonic() - t0
        if not v.formats:
            raise RuntimeError("no formats for the second video")
        first = self.first_info_seconds
        return (f"2nd video in {took:.1f}s (1st took {first:.1f}s"
                + (f", {first - took:+.1f}s" if first else "") + f"); '{v.title[:30]}'")

    async def video_hd(self) -> str:
        """What does a 4K video offer, and do our index parsers understand the REAL streams?
        For each codec family, fetch the first bytes of its best stream and locate the init/index ranges."""
        from .media import dash, isobmff, webm
        v = await self.extractor.video_info(TEST_VIDEO_HD)
        ladders = dash.video_ladders(v.formats)
        if not ladders:
            raise RuntimeError("no SDR video-only formats found")
        if self.dump_dir:
            self.dump_dir.mkdir(parents=True, exist_ok=True)
            (self.dump_dir / "video_hd_formats.json").write_text(json.dumps([
                {"id": f.format_id, "ext": f.ext, "vcodec": f.vcodec, "height": f.height, "fps": f.fps,
                 "tbr": f.tbr, "dynamic_range": f.dynamic_range, "note": f.note}
                for f in v.formats if f.has_video and not f.has_audio], indent=1))
        out = []
        for fam in dash.FAMILY_ORDER:
            if fam not in ladders:
                continue
            top = ladders[fam][max(ladders[fam])]

            async def fetch(a: int, b: int, url: str = top.url) -> bytes:
                async with self.http.stream(url, headers={"Range": f"bytes={a}-{b}"}) as r:
                    if r.status_code not in (200, 206):
                        raise RuntimeError(f"HTTP {r.status_code} fetching the {fam} stream")
                    return await r.aread()

            probe = webm.probe_ranges if top.ext == "webm" else isobmff.probe_ranges
            r = await probe(fetch)
            kind = "Cues" if top.ext == "webm" else "sidx"
            out.append(f"{fam} up to {top.height}p ({kind} at {r.index_start}-{r.index_end})")
        return "; ".join(out)

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

    async def exit_ip(self) -> str:
        """Which IP address did YouTube see? Its signed stream URLs carry the address that asked
        for them (``ip=...``), so this needs no third-party "what is my IP" site."""
        if not self.info:
            raise RuntimeError("skipped: video_info failed")
        ip = next((q["ip"][0] for f in self.info.formats
                   if (q := parse_qs(urlsplit(f.url).query)).get("ip")), None)
        if not ip:
            raise RuntimeError("stream URLs carry no ip= parameter, so this can't be determined")
        proxy = self.settings.outbound_proxy
        via = f"via proxy {re.sub(r'//[^@/]*@', '//***@', proxy)}" if proxy else "direct, no proxy configured"
        return f"YouTube saw {ip} ({via})"

    async def thumbnail(self) -> str:
        r = await self.http.get(f"https://i.ytimg.com/vi/{TEST_VIDEO}/hqdefault.jpg")
        r.raise_for_status()
        return f"{len(r.content)} bytes, {r.headers.get('content-type')}"

    async def channel(self) -> str:
        c = await self.extractor.resolve_channel(TEST_CHANNEL_HANDLE)
        return f"{TEST_CHANNEL_HANDLE} -> {c.id} '{c.title}', avatar {'yes' if c.avatar_url else 'no'}"

    async def _search(self, kind: str, cls: type) -> str:
        raw_fn = getattr(self.extractor, "raw_search", None)
        if raw_fn is None:
            raise RuntimeError("extractor has no search")
        raw = await raw_fn(TEST_QUERIES[kind], kind, 10)
        self._dump(f"search_{kind}", raw)
        from .extract.ytdlp import parse_entries
        items = parse_entries(raw)
        got = [i for i in items if isinstance(i, cls)]
        if not got:
            kinds = sorted({type(i).__name__ for i in items}) or ["nothing"]
            raise RuntimeError(f"no {cls.__name__} in results (parsed: {', '.join(kinds)}; "
                               f"{len(raw.get('entries') or [])} raw entries)")
        if kind == "playlist":
            self.playlist_id = got[0].id
        return f"{len(got)}/{len(items)} parsed as {cls.__name__}; first: '{got[0].title[:40]}'"

    async def search_video(self) -> str:
        return await self._search("video", VideoSummary)

    async def search_channel(self) -> str:
        return await self._search("channel", ChannelSummary)

    async def search_playlist(self) -> str:
        return await self._search("playlist", PlaylistSummary)

    async def channel_videos(self) -> str:
        from .extract.ytdlp import parse_entries
        raw = await self.extractor.raw_channel_videos(TEST_CHANNEL_ID, 10)  # type: ignore[attr-defined]
        self._dump("channel_videos", raw)
        vids = [i for i in parse_entries(raw) if isinstance(i, VideoSummary)]
        if not vids:
            raise RuntimeError(f"no videos parsed from {len(raw.get('entries') or [])} raw entries")
        return f"{len(vids)} videos; newest '{vids[0].title[:40]}', duration {vids[0].duration}s"

    async def channel_sort(self) -> str:
        """Popular/Oldest are built from the channel's uploads list. Does it exist, and do its entries carry
        the view counts we rank by?"""
        t0 = time.monotonic()
        raw = await self.extractor.raw_uploads(TEST_CHANNEL_ID, 200)  # type: ignore[attr-defined]
        self._dump("channel_uploads", {"entries": raw.get("entries", [])[:40], "playlist_count": raw.get("playlist_count")})
        from .extract.ytdlp import parse_entries
        vids = [i for i in parse_entries(raw) if isinstance(i, VideoSummary)]
        took = time.monotonic() - t0
        if len(vids) < 20:
            raise RuntimeError(f"the uploads list returned only {len(vids)} videos")
        with_views = [v for v in vids if v.view_count]
        if len(with_views) < 0.8 * len(vids):
            raise RuntimeError(f"only {len(with_views)}/{len(vids)} entries carry a view count, so Popular can't be ranked")
        top = max(with_views, key=lambda v: v.view_count or 0)
        if top.id == vids[0].id:
            raise RuntimeError("the most viewed video is also the newest: suspicious, ranking can't be told apart")
        return (f"{len(vids)} uploads in {took:.1f}s (channel total: {raw.get('playlist_count')}); {len(with_views)} with view "
                f"counts; most viewed '{top.title[:30]}' ({top.view_count:,})")

    async def playlist_videos(self) -> str:
        if not self.playlist_id:
            raise RuntimeError("skipped: playlist search found no playlist")
        from .extract.ytdlp import parse_entries
        raw = await self.extractor.raw_playlist(self.playlist_id, 10)  # type: ignore[attr-defined]
        self._dump("playlist", raw)
        vids = [i for i in parse_entries(raw) if isinstance(i, VideoSummary)]
        if not vids:
            raise RuntimeError(f"no videos parsed from {len(raw.get('entries') or [])} raw entries")
        return f"{self.playlist_id[:14]}...: {len(vids)} videos; first '{vids[0].title[:40]}'"

    async def comments_top(self) -> str:
        from .extract.ytdlp import parse_comments
        raw = await self.extractor.raw_comments(TEST_VIDEO, "top", 10)  # type: ignore[attr-defined]
        self._dump("comments_top", raw)
        top = [c for c in parse_comments(raw) if c.parent is None]
        if len(top) < 3:
            raise RuntimeError(f"only {len(top)} top-level comments parsed from "
                               f"{len(raw.get('comments') or [])} raw")
        c = top[0]
        return (f"{len(top)} comments; first by '{c.author}': '{c.text[:30]}', "
                f"{c.like_count} likes, '{c.time_text}'")

    async def comment_replies(self) -> str:
        from .extract.ytdlp import parse_comments
        raw = await self.extractor.raw_comments(  # type: ignore[attr-defined]
            TEST_VIDEO, "top", 10, replies_for=2, replies_per_thread=5)
        self._dump("comments_replies", raw)
        cs = parse_comments(raw)
        replies = [c for c in cs if c.parent]
        if not replies:
            raise RuntimeError(f"no replies among {len(cs)} comments fetched for the first 2 threads")
        parents = {c.id for c in cs if c.parent is None}
        orphans = [c for c in replies if c.parent not in parents]
        return (f"{len(replies)} replies under {len({c.parent for c in replies})} of the first "
                f"{len(parents)} comments" + (f" ({len(orphans)} orphaned!)" if orphans else ""))

    async def sponsorblock(self) -> str:
        """Hash-prefix lookup. The response also holds OTHER videos that share the prefix, so we use
        one of them to check that our parser copes with real submissions."""
        from .sponsorblock.provider import SponsorBlockApi, hash_prefix, parse_response
        api = SponsorBlockApi(self.http, RateLimiter(0.25, 0.1, 10**9))
        data = await api.raw(TEST_VIDEO)
        self._dump("sponsorblock_prefix", {"entries": data[:20] if isinstance(data, list) else data})
        ours = parse_response(data, TEST_VIDEO)
        head = f"prefix /{hash_prefix(TEST_VIDEO)}: {len(data)} video(s) share it; ours has {len(ours.segments)} segment(s)"
        others = [d for d in data if isinstance(d, dict) and d.get("videoID") != TEST_VIDEO and d.get("segments")]
        if not others:
            return head + " (nothing to test the parser on)"
        sample = parse_response(data, others[0]["videoID"])
        raw_n = len(others[0]["segments"])
        if len(sample.segments) + len(sample.full_labels) < 1:
            raise RuntimeError(f"{head}; parser found nothing in a real entry with {raw_n} raw segments")
        cats = sorted({g.category for g in sample.segments} | set(sample.full_labels))
        return f"{head}; parsed a real entry: {len(sample.segments)} segment(s) + {len(sample.full_labels)} full label(s), {', '.join(cats)}"

    async def run(self) -> list[Result]:
        for name, fn in [
            ("environment", self.environment), ("allowlist", self.allowlist),
            ("rss", self.rss), ("video_info", self.video_info), ("video_warm", self.video_info_warm),
            ("stream_range", self.stream_range), ("exit_ip", self.exit_ip), ("video_hd", self.video_hd), ("thumbnail", self.thumbnail),
            ("channel", self.channel), ("search_video", self.search_video),
            ("search_channel", self.search_channel), ("search_playlist", self.search_playlist),
            ("channel_videos", self.channel_videos), ("channel_sort", self.channel_sort), ("playlist", self.playlist_videos),
            ("comments_top", self.comments_top), ("comment_replies", self.comment_replies),
            ("sponsorblock", self.sponsorblock),
        ]:
            await self._check(name, fn)
        return self.results


def format_results(results: list[Result]) -> str:
    lines = []
    for r in results:
        lines.append(f"[{' OK ' if r.ok else 'FAIL'}] {r.name:<15}{r.seconds:5.1f}s  {r.detail}")
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
          f"{settings.outbound_proxy or 'none'}, ip_version: {settings.ip_version})\n")
    results = asyncio.run(main())
    print(format_results(results))
    return 0 if all(r.ok for r in results) else 1
