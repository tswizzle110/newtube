"""The ONE place the server makes outbound HTTP requests.

Everything that talks to the internet (RSS, thumbnails, SponsorBlock, video
bytes) uses ``OutboundClient``. yt-dlp does its own networking, so it is
configured from the same place via ``ytdlp_network_opts``.

Guarantees enforced here:
* https only, and only hosts on ALLOWED_HOST_SUFFIXES (checked on redirects too)
* one unremarkable browser User-Agent
* optional outbound proxy (``outbound_proxy`` setting): add a VPN/Tor/SOCKS later
  by changing one config value
* API-style requests go through the global rate limiter; media byte streams don't
  (a player issues many range requests and they aren't "page loads")
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..config import Settings
from .ratelimit import Lane, RateLimiter

# Host suffixes the server may contact. Nothing else, ever.
ALLOWED_HOST_SUFFIXES = (
    "youtube.com",
    "googlevideo.com",
    "ytimg.com",
    "ggpht.com",              # channel avatars
    "googleusercontent.com",  # channel avatars (alternate host)
    "sponsor.ajay.app",
)

# A common desktop Firefox string. Update occasionally; it just has to look ordinary.
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"
BASE_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
}


class HostNotAllowed(Exception):
    """Raised when code tries to contact a host outside the allowlist."""


def absolute_url(url: str | None) -> str | None:
    """YouTube returns channel avatars as protocol-relative URLs ("//yt3.ggpht.com/..."); the
    allowlist only accepts full https URLs, so complete them."""
    if url and url.startswith("//"):
        return "https:" + url
    return url


def check_url(url: str) -> None:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https":
        raise HostNotAllowed(f"only https is allowed: {url}")
    if not any(host == s or host.endswith("." + s) for s in ALLOWED_HOST_SUFFIXES):
        raise HostNotAllowed(f"host not on allowlist: {host}")


class _SilentLogger:
    """yt-dlp prints errors itself; we catch and report them, so drop its output."""

    def debug(self, msg: str) -> None: ...
    def warning(self, msg: str) -> None: ...
    def error(self, msg: str) -> None: ...


def source_address(settings: Settings) -> str | None:
    """The local address that forces IPv4 ("0.0.0.0") or IPv6 ("::"); None = let the system choose.
    Not used with a proxy, whose own connection decides the address YouTube sees."""
    if settings.outbound_proxy:
        return None
    return {"4": "0.0.0.0", "6": "::"}.get(settings.ip_version)


def ytdlp_network_opts(settings: Settings) -> dict[str, Any]:
    """Network-related yt-dlp options, so yt-dlp looks and behaves like the rest of us."""
    opts: dict[str, Any] = {
        "http_headers": dict(BASE_HEADERS),
        "socket_timeout": 20,
        "retries": 2,
        "logger": _SilentLogger(),
        "cachedir": False,   # don't write into ~/.cache
    }
    if settings.outbound_proxy:
        opts["proxy"] = settings.outbound_proxy
    if src := source_address(settings):
        opts["source_address"] = src
    return opts


async def _enforce_allowlist(request: httpx.Request) -> None:
    check_url(str(request.url))  # httpx runs this for every redirect hop as well


class OutboundClient:
    def __init__(
        self,
        settings: Settings,
        limiter: RateLimiter,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.limiter = limiter
        if transport is None and (src := source_address(settings)):
            transport = httpx.AsyncHTTPTransport(local_address=src)
        self._http = httpx.AsyncClient(
            headers=BASE_HEADERS,
            proxy=settings.outbound_proxy or None,
            transport=transport,
            timeout=httpx.Timeout(20.0, connect=10.0),
            follow_redirects=True,
            event_hooks={"request": [_enforce_allowlist]},
        )

    async def get(
        self, url: str, *, lane: Lane = Lane.INTERACTIVE, limited: bool = True, **kwargs: Any
    ) -> httpx.Response:
        """GET for API-style requests. ``limited=False`` skips the YouTube limiter: for other hosts
        (SponsorBlock), which the caller paces with its own limiter."""
        check_url(url)
        if limited:
            await self.limiter.acquire(lane)
        return await self._http.get(url, **kwargs)

    async def post(
        self, url: str, *, lane: Lane = Lane.INTERACTIVE, limited: bool = True, **kwargs: Any
    ) -> httpx.Response:
        check_url(url)
        if limited:
            await self.limiter.acquire(lane)
        return await self._http.post(url, **kwargs)

    async def get_media(self, url: str, **kwargs: Any) -> httpx.Response:
        """GET for static media (thumbnails, avatars). Allowlisted, NOT rate limited:
        a feed page legitimately loads dozens of images at once."""
        check_url(url)
        return await self._http.get(url, **kwargs)

    @asynccontextmanager
    async def stream(
        self, url: str, *, headers: dict[str, str] | None = None
    ) -> AsyncIterator[httpx.Response]:
        """Streamed GET for media bytes. Allowlisted but not rate limited."""
        check_url(url)
        async with self._http.stream("GET", url, headers=headers) as resp:
            yield resp

    async def open_stream(
        self, url: str, *, headers: dict[str, str] | None = None
    ) -> httpx.Response:
        """Like ``stream`` but the caller owns the response and MUST ``await resp.aclose()``.
        Used when the body is handed to a streaming HTTP response."""
        check_url(url)
        req = self._http.build_request("GET", url, headers=headers)
        return await self._http.send(req, stream=True)

    async def aclose(self) -> None:
        await self._http.aclose()
