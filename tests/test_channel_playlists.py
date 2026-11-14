"""The playlists a channel has made: read from its Playlists tab, cached, and shown on the channel page."""
import asyncio

from newtube.extract.base import BotCheck, ExtractionError, PlaylistSummary
from newtube.extract.ytdlp import YtDlpExtractor
from tests.test_api import env  # noqa: F401  (a fixture)

CH = "UC" + "a" * 22


class Ex:
    def __init__(self, playlists=None, fail=None):
        self.playlists, self.fail, self.calls = playlists or [], fail, 0

    async def channel_playlists(self, channel_id, limit=60, *, background=False):
        self.calls += 1
        if self.fail:
            raise self.fail
        return self.playlists


def pl(i, title="List", count=3):
    return PlaylistSummary("PL" + str(i).rjust(16, "x"), f"{title} {i}", "Chan", count, f"https://i.ytimg.com/vi/x{i}/hq.jpg")


def test_the_playlists_tab_is_read_as_playlists_with_the_owner_filled_in():
    async def raw(self, channel_id, limit=60, *, background=False):
        return {"channel": "Kitchen Science", "entries": [
            {"id": "PL" + "a" * 16, "title": "Sourdough basics", "url": "https://www.youtube.com/playlist?list=PL" + "a" * 16,
             "thumbnails": [{"url": "https://i.ytimg.com/vi/x/hq.jpg"}]},
            {"id": "e2eVIDEO001", "title": "a stray video row"},                 # not a playlist: ignored
            {"id": "PL" + "b" * 16, "title": "Bread", "playlist_count": 12},
            {"id": "PL" + "a" * 16, "title": "duplicate"}]}                     # the same playlist twice: kept once

    ex = object.__new__(YtDlpExtractor)
    ex.raw_channel_playlists = raw.__get__(ex)
    got = asyncio.run(ex.channel_playlists(CH))
    assert [(p.id, p.title, p.channel_title, p.video_count) for p in got] == [
        ("PL" + "a" * 16, "Sourdough basics", "Kitchen Science", None), ("PL" + "b" * 16, "Bread", "Kitchen Science", 12)]
    assert got[0].thumbnail_url == "https://i.ytimg.com/vi/x/hq.jpg"


def test_a_bad_channel_id_is_refused_before_anything_is_asked():
    ex = object.__new__(YtDlpExtractor)
    try:
        asyncio.run(ex.raw_channel_playlists("../etc/passwd"))
    except ExtractionError as e:
        assert "bad channel" in str(e)
    else:
        raise AssertionError("a bad id got through")


def test_the_api_returns_them_and_remembers_them_for_hours(env):
    client, ctx, _ex, seen = env
    ex = Ex([pl(1), pl(2)])
    ctx.lists.extractor = ex
    r = client.get(f"/api/channel/{CH}/playlists")
    assert r.status_code == 200 and [p["title"] for p in r.json()] == ["List 1", "List 2"]
    assert r.json()[0]["video_count"] == 3 and r.json()[0]["thumbnail_url"]
    client.get(f"/api/channel/{CH}/playlists"); client.get(f"/api/channel/{CH}/playlists")
    assert ex.calls == 1                                                       # three page views, one question to YouTube


def test_a_channel_with_no_playlists_is_an_empty_list_not_an_error(env):
    client, ctx, _ex, seen = env
    ctx.lists.extractor = Ex([])
    assert client.get(f"/api/channel/{CH}/playlists").json() == []


def test_bad_ids_failures_and_bot_checks(env):
    client, ctx, _ex, seen = env
    assert client.get("/api/channel/nope/playlists").status_code == 400
    ctx.lists.extractor = Ex(fail=ExtractionError("HTTP 500"))
    assert client.get(f"/api/channel/{CH}/playlists").status_code == 502
    ctx.lists.extractor = Ex(fail=BotCheck("Sign in to confirm you're not a bot"))
    r = client.get(f"/api/channel/{'UC' + 'b' * 22}/playlists")
    assert r.status_code == 429 and "not a bot" in r.json()["detail"]          # the same plain answer as everywhere else
