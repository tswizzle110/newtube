import httpx
import pytest

from newtube.config import Settings
from newtube.net.http import (
    USER_AGENT, HostNotAllowed, OutboundClient, check_url, ytdlp_network_opts,
)
from newtube.net.ratelimit import RateLimiter


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/feeds/videos.xml?channel_id=x",
    "https://rr1---sn-abc.googlevideo.com/videoplayback",
    "https://i.ytimg.com/vi/x/hq.jpg",
    "https://yt3.ggpht.com/a",
    "https://sponsor.ajay.app/api/skipSegments/abcd",
])
def test_allowed(url):
    check_url(url)


@pytest.mark.parametrize("url", [
    "http://www.youtube.com/",                 # not https
    "https://evil.com/",
    "https://youtube.com.evil.com/",           # suffix trick
    "https://notyoutube.com/",
    "https://www.google-analytics.com/",
    "https://127.0.0.1/",
])
def test_blocked(url):
    with pytest.raises(HostNotAllowed):
        check_url(url)


def _client(handler):
    limiter = RateLimiter(0, 0, 10)
    return OutboundClient(Settings(), limiter, transport=httpx.MockTransport(handler))


async def test_sends_browser_user_agent():
    seen = {}

    def handler(req):
        seen["ua"] = req.headers["user-agent"]
        return httpx.Response(200, text="ok")

    c = _client(handler)
    await c.get("https://www.youtube.com/")
    assert seen["ua"] == USER_AGENT


async def test_redirect_to_disallowed_host_is_blocked():
    def handler(req):
        if req.url.host == "www.youtube.com":
            return httpx.Response(302, headers={"location": "https://evil.com/x"})
        return httpx.Response(200)

    c = _client(handler)
    with pytest.raises(HostNotAllowed):
        await c.get("https://www.youtube.com/")


async def test_get_rejects_disallowed_host_before_any_request():
    called = []
    c = _client(lambda req: called.append(1) or httpx.Response(200))
    with pytest.raises(HostNotAllowed):
        await c.get("https://example.com/")
    assert not called


def test_ytdlp_opts_follow_proxy_setting():
    assert "proxy" not in ytdlp_network_opts(Settings())
    s = Settings(outbound_proxy="socks5://127.0.0.1:1080")
    assert ytdlp_network_opts(s)["proxy"] == "socks5://127.0.0.1:1080"
