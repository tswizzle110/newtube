import re
import xml.etree.ElementTree as ET

import httpx

from newtube.extract.base import Format, VideoInfo
from newtube.media.dash import NS
from tests.test_api import FakeExtractor, env, make_client  # noqa: F401  (env is a fixture)
from tests.test_dash import FTYP, MOOV, SIDX, box

MP4 = FTYP + MOOV + SIDX + box(b"mdat", b"d" * 500)


class DashExtractor(FakeExtractor):
    async def video_info(self, video_id, *, background=False):
        self.calls += 1
        sig = f"v{self.calls}"
        fm = [Format("137", f"https://rr1.googlevideo.com/v?sig={sig}", "mp4", "avc1.640028", None,
                     height=1080, width=1920, fps=30, tbr=4000),
              Format("140", f"https://rr1.googlevideo.com/a?sig={sig}", "m4a", None, "mp4a.40.2", tbr=128)]
        return VideoInfo(video_id, "T", None, None, "", 20, 1, 1, None, [], None, False, 0,
                         formats=fm, fetched_at=1)


def mp4_server(stale_sig="v1"):
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if f"sig={stale_sig}" in str(req.url):
            return httpx.Response(403)
        a, b = map(int, re.match(r"bytes=(\d+)-(\d+)", req.headers["range"]).groups())
        return httpx.Response(206, content=MP4[a:b + 1])
    return handler, seen


def test_manifest_built_from_probed_ranges_and_cached(tmp_path):
    handler, seen = mp4_server(stale_sig="none")
    with make_client(tmp_path, handler, DashExtractor()) as (client, ctx):
        r = client.get("/api/dash/aaaaaaaaaaa.mpd")
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/dash+xml")
        rep = ET.fromstring(r.text).find(f".//{{{NS}}}Representation[@id='137']")
        assert rep.find(f"{{{NS}}}SegmentBase").get("indexRange") == \
            f"{len(FTYP + MOOV)}-{len(FTYP + MOOV + SIDX) - 1}"
        assert "googlevideo" not in r.text
        probes = len(seen)
        client.get("/api/dash/aaaaaaaaaaa.mpd")
        assert len(seen) == probes           # second manifest: byte ranges came from the cache


def test_stale_urls_trigger_one_re_extraction(tmp_path):
    handler, seen = mp4_server(stale_sig="v1")      # first extraction's URLs are dead
    ex = DashExtractor()
    with make_client(tmp_path, handler, ex) as (client, ctx):
        assert client.get("/api/dash/aaaaaaaaaaa.mpd").status_code == 200
        assert ex.calls == 2


def test_no_dash_formats_gives_404_so_frontend_can_fall_back(env):
    client, *_ = env                                  # fixture video only has muxed itag 18
    assert client.get("/api/dash/aaaaaaaaaaa.mpd").status_code == 404


class MultiLangExtractor(DashExtractor):
    async def video_info(self, video_id, *, background=False):
        info = await super().video_info(video_id)
        aud = lambda fid, lang, pref, note: Format(fid, f"https://rr1.googlevideo.com/{fid}?sig=ok", "m4a",
                                                   None, "mp4a.40.2", tbr=128, language=lang,
                                                   language_preference=pref, note=note)
        info.formats = [f for f in info.formats if f.format_id != "140"] + [
            aud("140-0", "en-US", 10, "English (US) original (default), medium"),
            aud("140-1", "es-US", -1, "Spanish (US), medium"),
            aud("140-2", "de-DE", -1, "German (DE), medium"),
            aud("140-drc", "en-US", 10, "English (US) original (default), medium, DRC")]
        return info


def test_manifest_defaults_to_original_audio_and_probes_only_one_audio_stream(tmp_path):
    handler, seen = mp4_server(stale_sig="none")
    with make_client(tmp_path, handler, MultiLangExtractor()) as (client, ctx):
        mpd = client.get("/api/dash/aaaaaaaaaaa.mpd").text
        ids = [r.get("id") for r in ET.fromstring(mpd).iter(f"{{{NS}}}Representation")]
        assert ids == ["137", "140-0"]                       # 1 video + the original-language audio
        assert len(seen) == 2                                # not one request per dub
        es = client.get("/api/dash/aaaaaaaaaaa.mpd?lang=es-US").text
        assert [r.get("id") for r in ET.fromstring(es).iter(f"{{{NS}}}Representation")] == ["137", "140-1"]
        assert client.get("/api/dash/aaaaaaaaaaa.mpd?lang=../../x").status_code == 400
        meta = client.get("/api/video/aaaaaaaaaaa").json()
        assert [(t["lang"], t["original"]) for t in meta["audio_tracks"]][0] == ("en-US", True)
        assert {t["lang"] for t in meta["audio_tracks"]} == {"en-US", "es-US", "de-DE"}
