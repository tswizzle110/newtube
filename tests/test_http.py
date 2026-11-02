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


# ---- ip_version -------------------------------------------------------------------------------

from newtube.config import load_settings  # noqa: E402
from newtube.net.http import source_address  # noqa: E402


def test_source_address_for_each_ip_version():
    assert source_address(Settings()) is None                       # auto: the system decides
    assert source_address(Settings(ip_version="4")) == "0.0.0.0"
    assert source_address(Settings(ip_version="6")) == "::"
    assert source_address(Settings(ip_version="4", outbound_proxy="http://127.0.0.1:8888")) is None   # proxy decides


def test_yt_dlp_is_told_the_same(monkeypatch):
    assert "source_address" not in ytdlp_network_opts(Settings())
    assert ytdlp_network_opts(Settings(ip_version="4"))["source_address"] == "0.0.0.0"
    assert "source_address" not in ytdlp_network_opts(Settings(ip_version="4", outbound_proxy="http://p:1"))


def test_app_http_client_binds_to_the_requested_family(monkeypatch):
    seen = {}
    real = httpx.AsyncHTTPTransport

    def spy(*a, **k):
        seen.update(k)
        return real(*a, **k)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", spy)
    OutboundClient(Settings(ip_version="4"), RateLimiter(0, 0, 1))
    assert seen["local_address"] == "0.0.0.0"
    seen.clear()
    OutboundClient(Settings(), RateLimiter(0, 0, 1))
    assert "local_address" not in seen


def test_ip_version_comes_from_config_and_is_validated(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert load_settings({}).ip_version == "auto"
    assert load_settings({"NEWTUBE_IP_VERSION": "4"}).ip_version == "4"
    (tmp_path / "newtube.toml").write_text('ip_version = "6"\n')
    assert load_settings({}).ip_version == "6"
    with pytest.raises(ValueError, match="ip_version"):
        load_settings({"NEWTUBE_IP_VERSION": "5"})
