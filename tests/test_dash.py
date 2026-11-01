import struct
import xml.etree.ElementTree as ET

import pytest

from newtube.extract.base import Format
from newtube.media.dash import NS, Rep, build_mpd, select_formats
from newtube.media.isobmff import NeedMore, NotDashMp4, Ranges, parse, probe_ranges


def box(typ: bytes, payload: bytes = b"") -> bytes:
    return struct.pack(">I4s", 8 + len(payload), typ) + payload


FTYP, MOOV, SIDX = box(b"ftyp", b"x" * 16), box(b"moov", b"y" * 100), box(b"sidx", b"z" * 40)
MP4 = FTYP + MOOV + SIDX + box(b"moof", b"m" * 10) + box(b"mdat", b"d" * 1000)


def test_parse_finds_init_and_index_ranges():
    r = parse(MP4)
    assert r == Ranges(init_end=len(FTYP + MOOV) - 1, index_start=len(FTYP + MOOV),
                       index_end=len(FTYP + MOOV + SIDX) - 1)


def test_parse_asks_for_more_when_truncated():
    cut = MP4[: len(FTYP) + 50]                       # inside moov
    r = parse(cut)
    assert isinstance(r, NeedMore) and r.bytes_needed >= len(FTYP) + len(MOOV)
    inside_sidx = MP4[: len(FTYP + MOOV) + 20]
    r = parse(inside_sidx)
    assert isinstance(r, NeedMore) and r.bytes_needed == len(FTYP + MOOV + SIDX)


def test_parse_rejects_non_indexed_mp4():
    with pytest.raises(NotDashMp4):
        parse(FTYP + MOOV + box(b"mdat", b"d" * 50))        # progressive file, no sidx


def test_parse_handles_largesize_box():
    big_free = struct.pack(">I4sQ", 1, b"free", 16 + 5) + b"a" * 5
    r = parse(FTYP + big_free + SIDX)
    assert r.index_start == len(FTYP + big_free)


async def test_probe_ranges_grows_the_read_until_sidx_fits():
    big_moov = box(b"moov", b"y" * 40_000)               # bigger than the first 32 KiB read
    data = FTYP + big_moov + SIDX + box(b"mdat", b"d" * 100)
    calls = []

    async def fetch(a, b):
        calls.append((a, b))
        return data[a:b + 1]

    r = await probe_ranges(fetch)
    assert r.index_start == len(FTYP + big_moov) and len(calls) == 2


async def test_probe_gives_up_on_short_file_without_sidx():
    async def fetch(a, b):
        return FTYP + MOOV
    with pytest.raises(NotDashMp4):
        await probe_ranges(fetch)


def F(fid, ext, v, a, h=None, tbr=1, lang=None):
    return Format(fid, "https://x.googlevideo.com/" + fid, ext, v, a, height=h, width=(h or 0) * 16 // 9,
                  fps=30, tbr=tbr, language=lang)


def test_select_formats_picks_h264_mp4_and_aac_only():
    fmts = [
        F("137", "mp4", "avc1.640028", None, 1080, 4000), F("136", "mp4", "avc1.4d401f", None, 720, 2000),
        F("299", "mp4", "avc1.64002a", None, 1080, 6000),            # same height, higher bitrate wins
        F("248", "webm", "vp09.00", None, 1080, 3000), F("399", "mp4", "av01.0", None, 1080, 2500),
        F("18", "mp4", "avc1", "mp4a", 360),                          # muxed: not for DASH
        F("140", "m4a", None, "mp4a.40.2", tbr=128), F("251", "webm", None, "opus", tbr=130),
    ]
    video, audio = select_formats(fmts)
    assert [f.format_id for f in video] == ["136", "299"]
    assert [f.format_id for f in audio] == ["140"]


def test_build_mpd_is_valid_xml_with_proxy_urls_and_ranges():
    v = Rep(F("137", "mp4", "avc1.640028", None, 1080, 4000), Ranges(999, 1000, 1500))
    a = Rep(F("140", "m4a", None, "mp4a.40.2", tbr=128, lang="en"), Ranges(700, 701, 900))
    root = ET.fromstring(build_mpd("aaaaaaaaaaa", 19.5, [v], [a]))
    ns = {"m": NS}
    assert root.get("mediaPresentationDuration") == "PT19.500S"
    reps = root.findall(".//m:Representation", ns)
    assert [r.get("id") for r in reps] == ["137", "140"]
    assert reps[0].find("m:BaseURL", ns).text == "/api/stream/aaaaaaaaaaa/137"
    assert reps[0].find("m:SegmentBase", ns).get("indexRange") == "1000-1500"
    assert reps[0].find("m:SegmentBase/m:Initialization", ns).get("range") == "0-999"
    assert reps[0].get("height") == "1080" and reps[0].get("bandwidth") == "4000000"
    assert root.findall(".//m:AdaptationSet", ns)[1].get("lang") == "en"
    assert "googlevideo" not in build_mpd("aaaaaaaaaaa", 1, [v], [a])
