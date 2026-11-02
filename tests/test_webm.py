"""WebM index location (VP9 DASH), codec-family selection, and the manifest for 1440p/4K."""
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

import httpx
import pytest

from newtube.extract.base import Format
from newtube.media.dash import (
    NS, Rep, available_summary, build_mpd, choose_family, select_formats, video_ladders,
)
from newtube.media.isobmff import NeedMore, Ranges
from newtube.media.webm import (
    CLUSTER, CUES, NotDashWebm, SEGMENT, parse, probe_ranges,
)
from tests.test_api import FakeExtractor, _Body, make_client
from tests.test_dash import F, box

# ---- building EBML by hand ---------------------------------------------------------------------


def vint(n: int) -> bytes:
    """An EBML size: the shortest length-prefixed big-endian integer that holds ``n``."""
    for length in range(1, 9):
        if n < (1 << (7 * length)) - 1:                    # all-ones is reserved for "unknown size"
            return (n | (1 << (7 * length))).to_bytes(length, "big")       # the marker bit says how long it is
    raise ValueError(n)


def el(eid: int, payload: bytes = b"") -> bytes:
    return eid.to_bytes((eid.bit_length() + 7) // 8, "big") + vint(len(payload)) + payload


UNKNOWN8 = bytes([0x01, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])        # "size unknown"
EBML = el(0x1A45DFA3, b"\x42\x82\x84webm")
SEG_HEAD = SEGMENT.to_bytes(4, "big") + UNKNOWN8
SEEKHEAD, INFO, TRACKS = el(0x114D9B74, b"s" * 30), el(0x1549A966, b"i" * 40), el(0x1654AE6B, b"t" * 120)
VOID = el(0xEC, b"\x00" * 50)
CUES_EL = el(CUES, b"c" * 90)
CLUSTER_EL = el(CLUSTER, b"d" * 300)
WEBM = EBML + SEG_HEAD + SEEKHEAD + VOID + INFO + TRACKS + CUES_EL + CLUSTER_EL


def test_finds_init_and_cues_ranges():
    before = len(EBML + SEG_HEAD + SEEKHEAD + VOID + INFO + TRACKS)
    assert parse(WEBM) == Ranges(init_end=before - 1, index_start=before, index_end=before + len(CUES_EL) - 1)


def test_asks_for_more_when_the_cues_are_cut_off():
    cut = WEBM[: len(WEBM) - len(CLUSTER_EL) - 20]                        # inside the Cues
    r = parse(cut)
    assert isinstance(r, NeedMore) and r.bytes_needed == len(WEBM) - len(CLUSTER_EL)
    assert isinstance(parse(WEBM[:10]), NeedMore) and isinstance(parse(WEBM[:len(EBML) + 3]), NeedMore)


@pytest.mark.parametrize("data", [
    EBML + SEG_HEAD + INFO + TRACKS + CLUSTER_EL + CUES_EL,               # media before the index
    b"\x00" * 64,                                                         # not EBML at all
    box(b"ftyp", b"mp42" * 4) * 3,                                        # an MP4
    EBML + el(0x1F43B675, b"x"),                                          # no Segment
])
def test_rejects_files_without_leading_cues(data):
    with pytest.raises(NotDashWebm):
        parse(data)


async def test_probe_grows_the_read_until_the_cues_fit():
    big = EBML + SEG_HEAD + SEEKHEAD + el(0x1654AE6B, b"t" * 70_000) + el(CUES, b"c" * 5000) + CLUSTER_EL
    calls = []

    async def fetch(a, b):
        calls.append(b + 1)
        return big[a:b + 1]
    r = await probe_ranges(fetch)
    assert big[r.index_start:r.index_start + 4] == CUES.to_bytes(4, "big") and len(calls) >= 2


# ---- the real thing: whatever ffmpeg's WebM muxer writes ----------------------------------------

@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_parses_a_real_vp9_webm_made_by_ffmpeg(tmp_path):
    out = tmp_path / "v.webm"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=6",
                    "-c:v", "libvpx-vp9", "-g", "25", "-b:v", "200k", "-f", "webm", "-dash", "1", "-cues_to_front", "1", str(out)],
                   check=True)
    data = out.read_bytes()
    r = parse(data[:65536])
    assert isinstance(r, Ranges)
    assert data[r.index_start:r.index_start + 4] == CUES.to_bytes(4, "big")                   # the index starts with a Cues element
    assert data[r.init_end + 1:r.init_end + 5] == CUES.to_bytes(4, "big")                     # directly after the init range
    assert data[r.index_end + 1:r.index_end + 5] == CLUSTER.to_bytes(4, "big")                # and media follows it
    assert b"\x16\x54\xae\x6b" in data[:r.init_end + 1]                                       # Tracks are inside the init range


# ---- choosing a codec family -----------------------------------------------------------------

def V(fid, ext, vcodec, h, tbr=1000, dyn="SDR"):
    return Format(fid, "https://x.googlevideo.com/" + fid, ext, vcodec, None, height=h, width=h * 16 // 9, fps=30, tbr=tbr,
                  dynamic_range=dyn)


LADDER = [
    V("137", "mp4", "avc1.640028", 1080, 4000), V("136", "mp4", "avc1.4d401f", 720, 2000),
    V("248", "webm", "vp09.00.40.08", 1080, 3000), V("271", "webm", "vp09.00.50.08", 1440, 6000),
    V("313", "webm", "vp09.00.51.08", 2160, 15000), V("244", "webm", "vp09.00.31.08", 480, 600),
    V("401", "mp4", "av01.0.12M.08", 2160, 9000), V("400", "mp4", "av01.0.08M.08", 1440, 5000),
    V("337", "webm", "vp09.02.51.10", 2160, 20000, dyn="HDR10"),                # HDR: never offered
    V("399", "webm", "av01.0.08M.08", 1440, 5000),                              # AV1 in WebM: not a YouTube combo
    F("140", "m4a", None, "mp4a.40.2", tbr=128),
]


def test_auto_picks_the_family_that_reaches_the_highest_resolution():
    video, audio = select_formats(LADDER)
    assert [f.format_id for f in video] == ["244", "248", "271", "313"] and video[-1].height == 2160   # all VP9 rungs
    assert [f.format_id for f in audio] == ["140"]
    assert all(f.dynamic_range == "SDR" for f in video)


def test_when_resolution_ties_the_most_compatible_codec_wins():
    two = [f for f in LADDER if f.format_id in ("137", "248", "140")]
    assert [f.format_id for f in select_formats(two)[0]] == ["137"]             # H.264 beats VP9 at equal height


def test_explicit_codec_choices_and_fallbacks():
    assert [f.format_id for f in select_formats(LADDER, codec="avc1")[0]] == ["136", "137"]
    assert [f.format_id for f in select_formats(LADDER, codec="av1")[0]] == ["400", "401"]
    only_h264 = [f for f in LADDER if f.format_id in ("137", "136", "140")]
    assert [f.format_id for f in select_formats(only_h264, codec="vp9")[0]] == ["136", "137"]   # unavailable -> auto
    assert [f.format_id for f in select_formats(LADDER, codec="nonsense")[0]][-1] == "313"


def test_max_height_limits_the_ladder_and_can_change_the_winner():
    assert [f.height for f in select_formats(LADDER, max_height=1440)[0]] == [480, 1080, 1440]   # VP9, capped
    assert [f.format_id for f in select_formats(LADDER, max_height=1080)[0]] == ["136", "137"]    # tie at 1080 -> H.264
    assert select_formats(LADDER, max_height=360)[0] == []                                        # nothing that low


def test_summary_and_ladders_ignore_hdr_and_odd_containers():
    assert available_summary(LADDER) == {"avc1": 1080, "vp9": 2160, "av1": 2160}
    assert 337 not in {f.format_id for rungs in video_ladders(LADDER).values() for f in rungs.values()}
    assert choose_family({}, "auto") is None


def test_manifest_uses_the_webm_mime_for_vp9_and_mp4_for_the_others():
    r = lambda f: Rep(f, Ranges(99, 100, 200))
    vp9 = ET.fromstring(build_mpd("aaaaaaaaaaa", 10, [r(LADDER[3])], [r(LADDER[-1])]))
    sets = {a.get("contentType"): a.get("mimeType") for a in vp9.iter(f"{{{NS}}}AdaptationSet")}
    assert sets == {"video": "video/webm", "audio": "audio/mp4"}                  # WebM video + AAC audio is fine in MSE
    mp4 = ET.fromstring(build_mpd("aaaaaaaaaaa", 10, [r(LADDER[0])], [r(LADDER[-1])]))
    assert {a.get("mimeType") for a in mp4.iter(f"{{{NS}}}AdaptationSet") if a.get("contentType") == "video"} == {"video/mp4"}


# ---- end to end through the API ---------------------------------------------------------------

MP4 = box(b"ftyp", b"x" * 16) + box(b"moov", b"y" * 100) + box(b"sidx", b"z" * 40) + box(b"mdat", b"d" * 200)


class HdExtractor(FakeExtractor):
    async def video_info(self, video_id, *, background=False):
        info = await super().video_info(video_id)
        u = lambda n: f"https://rr1.googlevideo.com/{n}?sig=ok"
        info.formats = [
            Format("137", u("h264"), "mp4", "avc1.640028", None, height=1080, width=1920, fps=30, tbr=4000, dynamic_range="SDR"),
            Format("313", u("vp9-4k"), "webm", "vp09.00.51.08", None, height=2160, width=3840, fps=30, tbr=15000, dynamic_range="SDR"),
            Format("271", u("vp9-2k"), "webm", "vp09.00.50.08", None, height=1440, width=2560, fps=30, tbr=6000, dynamic_range="SDR"),
            Format("140", u("aac"), "m4a", None, "mp4a.40.2", tbr=128)]
        return info


def hd_handler(req):
    if "googlevideo" not in str(req.url):
        return httpx.Response(404)
    a, b = map(int, re.match(r"bytes=(\d+)-(\d+)", req.headers["range"]).groups())
    data = WEBM if "vp9" in req.url.path else MP4
    return httpx.Response(206, stream=_Body(data[a:b + 1]))


def reps(mpd_text):
    return {r.get("id"): r for r in ET.fromstring(mpd_text).iter(f"{{{NS}}}Representation")}


def test_manifest_offers_vp9_up_to_4k_and_uses_the_cues_range(tmp_path):
    with make_client(tmp_path, hd_handler, HdExtractor()) as (client, ctx):
        r = client.get("/api/dash/aaaaaaaaaaa.mpd")
        assert r.status_code == 200
        got = reps(r.text)
        assert set(got) == {"271", "313", "140"}                       # VP9 ladder + the one audio stream; no H.264
        seg = got["313"].find(f"{{{NS}}}SegmentBase")
        before = len(EBML + SEG_HEAD + SEEKHEAD + VOID + INFO + TRACKS)
        assert seg.get("indexRange") == f"{before}-{before + len(CUES_EL) - 1}"
        assert seg.find(f"{{{NS}}}Initialization").get("range") == f"0-{before - 1}"
        assert got["313"].get("height") == "2160" and "vp09" in got["313"].get("codecs")


def test_settings_change_what_the_manifest_offers(tmp_path):
    with make_client(tmp_path, hd_handler, HdExtractor()) as (client, ctx):
        cfg = client.get("/api/settings/playback").json()
        assert cfg["codec"] == "auto" and cfg["max_height"] == 0 and "vp9" in cfg["codecs"]
        assert client.put("/api/settings/playback", json={"max_height": 1440}).json()["max_height"] == 1440
        assert set(reps(client.get("/api/dash/aaaaaaaaaaa.mpd").text)) == {"271", "140"}          # 4K left out
        client.put("/api/settings/playback", json={"codec": "avc1", "max_height": 0})
        assert set(reps(client.get("/api/dash/aaaaaaaaaaa.mpd").text)) == {"137", "140"}          # H.264 only
        assert set(reps(client.get("/api/dash/aaaaaaaaaaa.mpd?codec=vp9").text)) == {"271", "313", "140"}   # override
        assert client.get("/api/dash/aaaaaaaaaaa.mpd?codec=divx").status_code == 400


@pytest.mark.parametrize("bad", [{"codec": "divx"}, {"max_height": 999}, {"max_height": "1080"}, {"max_height": True}, {"other": 1}, "x"])
def test_invalid_playback_settings_are_rejected(tmp_path, bad):
    with make_client(tmp_path, hd_handler, HdExtractor()) as (client, ctx):
        assert client.put("/api/settings/playback", json=bad).status_code in (400, 422)
        assert client.get("/api/settings/playback").json()["codec"] == "auto"


def test_a_corrupt_stored_playback_setting_cannot_break_playback(tmp_path):
    from newtube.db.queries import settings as kv
    with make_client(tmp_path, hd_handler, HdExtractor()) as (client, ctx):
        kv.put(ctx.conn, "playback", {"codec": "garbage", "max_height": -5})
        assert client.get("/api/dash/aaaaaaaaaaa.mpd").status_code == 200
