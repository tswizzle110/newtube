"""Proof that `outbound_proxy` really captures ALL outbound traffic (app HTTP client AND yt-dlp).

A fake proxy listens on localhost and records what it is asked to CONNECT to, then refuses.
If anything bypassed the proxy it would not appear here (and would try the real network)."""

import asyncio

import pytest

from newtube.config import Settings
from newtube.extract.base import ExtractionError
from newtube.extract.ytdlp import YtDlpExtractor
from newtube.net.http import OutboundClient
from newtube.net.ratelimit import RateLimiter


@pytest.fixture
async def fake_proxy(monkeypatch):
    # Environment proxies must not be what makes these tests pass: only the explicit setting counts.
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    seen: list[str] = []

    async def handle(reader, writer):
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            seen.append(head.split(b"\r\n")[0].decode())
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
        except Exception:  # noqa: BLE001 - a test double
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}", seen
    server.close()
    await server.wait_closed()


async def test_app_http_client_goes_through_the_proxy_with_the_hostname(fake_proxy):
    url, seen = fake_proxy
    client = OutboundClient(Settings(outbound_proxy=url), RateLimiter(0, 0, 10))
    with pytest.raises(Exception):                       # the fake proxy answers 502
        await client.get("https://www.youtube.com/feeds/videos.xml?channel_id=UCBR8-60-B28hp2BmDPdntcQ")
    with pytest.raises(Exception):
        await client.get_media("https://i.ytimg.com/vi/jNQXAC9IVRw/hqdefault.jpg")
    await client.aclose()
    # hostnames (not IPs) reach the proxy: DNS is resolved on the VPN side, never locally
    assert seen == ["CONNECT www.youtube.com:443 HTTP/1.1", "CONNECT i.ytimg.com:443 HTTP/1.1"]


async def test_yt_dlp_goes_through_the_proxy_too(fake_proxy):
    url, seen = fake_proxy
    ex = YtDlpExtractor(Settings(outbound_proxy=url), RateLimiter(0, 0, 10))
    with pytest.raises(ExtractionError):
        await ex.video_info("jNQXAC9IVRw")
    assert seen, "yt-dlp never contacted the proxy"
    assert all(line.startswith("CONNECT ") and line.endswith(":443 HTTP/1.1") for line in seen), seen
    assert {line.split()[1].rsplit(":", 1)[0] for line in seen} <= {"www.youtube.com", "youtubei.googleapis.com", "i.ytimg.com", "m.youtube.com"}, seen


async def test_media_streaming_uses_the_proxy_as_well(fake_proxy):
    url, seen = fake_proxy
    client = OutboundClient(Settings(outbound_proxy=url), RateLimiter(0, 0, 10))
    with pytest.raises(Exception):
        await client.open_stream("https://rr1---sn-abc.googlevideo.com/videoplayback", headers={"Range": "bytes=0-9"})
    await client.aclose()
    assert seen == ["CONNECT rr1---sn-abc.googlevideo.com:443 HTTP/1.1"]
