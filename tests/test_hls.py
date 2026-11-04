"""HLS playlists for Apple TV / Safari: built from the same fragmented MP4 streams the DASH manifest uses."""

import re
import struct

import httpx
import pytest

from newtube.extract.base import Format, VideoInfo
from newtube.media import hls
from newtube.media.isobmff import Ranges
from tests.test_api import FakeExtractor, make_client
from tests.test_dash import FTYP, MOOV, box


def sidx_box(entries, *, timescale=1000, first_offset=0, version=0):
    """A real sidx box listing (size, duration) fragments."""
    body = struct.pack(">I", 0) + struct.pack(">II", 1, timescale)
    body += struct.pack(">II", 0, first_offset) if version == 0 else struct.pack(">QQ", 0, first_offset)
    body += struct.pack(">HH", 0, len(entries))
    for size, dur in entries:
        body += struct.pack(">III", size, dur, 0x90000000)
    return box(b"sidx", bytes([version, 0, 0, 0]) + body[4:])


FRAGS = [(300, 4000), (200, 4000), (100, 2500)]
SIDX = sidx_box(FRAGS)
MEDIA = box(b"moof", b"m" * 292) + box(b"moof", b"m" * 192) + box(b"moof", b"m" * 92)   # sizes 300, 200, 100
FILE = FTYP + MOOV + SIDX + MEDIA


def test_sidx_gives_byte_ranges_and_durations():
    segs = hls.parse_sidx(SIDX, len(FTYP + MOOV))
    start = len(FTYP + MOOV + SIDX)
    assert [(s.start, s.size, s.seconds) for s in segs] == [(start, 300, 4.0), (start + 300, 200, 4.0),
                                                            (start + 500, 100, 2.5)]


def test_sidx_version_1_and_first_offset():
    segs = hls.parse_sidx(sidx_box([(50, 1000)], first_offset=7, version=1), 10)
    assert segs[0].start == 10 + len(sidx_box([(50, 1000)], version=1)) + 7


def test_unreadable_or_hierarchical_sidx_is_rejected():
    with pytest.raises(hls.BadSidx):
        hls.parse_sidx(b"garbage-not-a-box", 0)
    hier = box(b"sidx", bytes([0, 0, 0, 0]) + struct.pack(">IIIIHH", 1, 1000, 0, 0, 0, 1)
               + struct.pack(">III", 0x80000000 | 100, 1000, 0))
    with pytest.raises(hls.BadSidx):
        hls.parse_sidx(hier, 0)


def test_media_playlist_is_a_byte_range_vod_playlist():
    fmt = Format("137", "https://x", "mp4", "avc1.640028", None, height=1080, width=1920, fps=30, tbr=4000)
    segs = hls.parse_sidx(SIDX, len(FTYP + MOOV))
    text = hls.media_playlist("aaaaaaaaaaa", fmt, Ranges(len(FTYP + MOOV) - 1, len(FTYP + MOOV),
                                                          len(FTYP + MOOV + SIDX) - 1), segs)
    lines = text.splitlines()
    assert lines[0] == "#EXTM3U" and lines[-1] == "#EXT-X-ENDLIST" and "#EXT-X-PLAYLIST-TYPE:VOD" in lines
    assert "#EXT-X-TARGETDURATION:4" in lines
    assert f'#EXT-X-MAP:URI="/api/stream/aaaaaaaaaaa/137",BYTERANGE="{len(FTYP + MOOV)}@0"' in lines
    assert lines.count("/api/stream/aaaaaaaaaaa/137") == 3
    assert "#EXTINF:2.500," in lines and "googlevideo" not in text
    start = len(FTYP + MOOV + SIDX)
    assert f"#EXT-X-BYTERANGE:200@{start + 300}" in lines


def test_master_playlist_lists_each_resolution_with_codecs_and_the_audio_group():
    video = [Format("137", "u", "mp4", "avc1.640028", None, height=1080, width=1920, fps=30, tbr=4000),
             Format("135", "u", "mp4", "avc1.4d401f", None, height=480, width=854, fps=30, tbr=800)]
    audio = Format("140", "u", "m4a", None, "mp4a.40.2", tbr=128, language="en")
    text = hls.master_playlist("aaaaaaaaaaa", video, audio, "en")
    assert 'CODECS="avc1.4d401f,mp4a.40.2"' in text and "RESOLUTION=1920x1080" in text
    assert text.index("/135.m3u8") < text.index("/137.m3u8")                      # low to high
    assert 'URI="/api/hls/aaaaaaaaaaa/140.m3u8"' in text and text.count('AUDIO="aud"') == 2
    assert "BANDWIDTH=4128000" in text


class HlsExtractor(FakeExtractor):
    async def video_info(self, video_id, *, background=False):
        self.calls += 1
        fm = [Format("137", "https://rr1.googlevideo.com/v", "mp4", "avc1.640028", None,
                     height=1080, width=1920, fps=30, tbr=4000),
              Format("248", "https://rr1.googlevideo.com/w", "webm", "vp9", None, height=1080, width=1920, tbr=3000),
              Format("140", "https://rr1.googlevideo.com/a", "m4a", None, "mp4a.40.2", tbr=128)]
        return VideoInfo(video_id, "T", None, None, "", 10, 1, 1, None, [], None, False, 0, formats=fm, fetched_at=1)


def file_server():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        a, b = map(int, re.match(r"bytes=(\d+)-(\d+)", req.headers["range"]).groups())
        return httpx.Response(206, content=FILE[a:b + 1])
    return handler, seen


def test_endpoints_serve_a_master_and_track_playlists_and_cache_the_index(tmp_path):
    handler, seen = file_server()
    with make_client(tmp_path, handler, HlsExtractor()) as (client, ctx):
        m = client.get("/api/hls/aaaaaaaaaaa.m3u8")
        assert m.status_code == 200 and m.headers["content-type"].startswith("application/vnd.apple.mpegurl")
        assert "/api/hls/aaaaaaaaaaa/137.m3u8" in m.text and "248" not in m.text      # H.264 only, never VP9
        assert "googlevideo" not in m.text
        t = client.get("/api/hls/aaaaaaaaaaa/137.m3u8")
        assert t.status_code == 200 and t.text.count("#EXTINF") == 3
        n = len(seen)
        client.get("/api/hls/aaaaaaaaaaa/137.m3u8")
        assert len(seen) == n                                                          # layout and index were cached


def test_bad_ids_and_unknown_tracks(tmp_path):
    handler, _ = file_server()
    with make_client(tmp_path, handler, HlsExtractor()) as (client, ctx):
        assert client.get("/api/hls/bad.m3u8").status_code == 400
        assert client.get("/api/hls/aaaaaaaaaaa.m3u8?lang=a%20b").status_code == 400
        assert client.get("/api/hls/aaaaaaaaaaa/999.m3u8").status_code == 404
        assert client.get("/api/hls/aaaaaaaaaaa/248.m3u8").status_code == 404          # WebM is not HLS-capable


def test_no_h264_means_404(env):
    client, *_ = env                    # the fixture video only has a muxed format
    assert client.get("/api/hls/aaaaaaaaaaa.m3u8").status_code == 404


from tests.test_api import env  # noqa: E402,F401
