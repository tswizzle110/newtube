"""Build HLS (fragmented-MP4) playlists for one video, for players that cannot do DASH (Apple TV, Safari/AirPlay).

Nothing is re-encoded or re-muxed. YouTube's DASH MP4 streams are already fragmented MP4 with a ``sidx`` index listing
every fragment's size and duration, which is exactly what HLS byte-range playlists need: each ``#EXTINF`` segment is one
byte range of the same /api/stream/<video>/<format> proxy URL the web player uses, and ``#EXT-X-MAP`` is the init range.

Only H.264 + AAC is offered: that is what AVPlayer reliably plays (it cannot play VP9, and YouTube serves no HEVC).
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

from ..extract.base import Format
from .dash import AudioTrack  # noqa: F401  (re-exported for callers that list tracks)
from .isobmff import Ranges


class BadSidx(Exception):
    """The index box could not be read (not the layout we expect): the caller falls back to "no HLS"."""


@dataclass(frozen=True)
class Segment:
    start: int         # byte offset of the fragment in the file
    size: int
    seconds: float


def parse_sidx(data: bytes, index_start: int) -> list[Segment]:
    """Fragments listed by the sidx box that begins at byte ``index_start`` (``data`` is exactly that box)."""
    try:
        size, typ = struct.unpack(">I4s", data[:8])
        if typ != b"sidx" or size > len(data):
            raise BadSidx("not a sidx box")
        version = data[8]
        timescale = struct.unpack(">I", data[16:20])[0]
        if version == 0:
            first_offset = struct.unpack(">I", data[24:28])[0]
            pos = 28
        else:
            first_offset = struct.unpack(">Q", data[28:36])[0]
            pos = 36
        count = struct.unpack(">H", data[pos + 2:pos + 4])[0]
        pos += 4
        if timescale == 0:
            raise BadSidx("zero timescale")
        offset = index_start + size + first_offset      # (offsets are relative to the end of the sidx box)
        out: list[Segment] = []
        for _ in range(count):
            ref, duration, _sap = struct.unpack(">III", data[pos:pos + 12])
            pos += 12
            if ref >> 31:
                raise BadSidx("hierarchical sidx")
            length = ref & 0x7FFFFFFF
            out.append(Segment(offset, length, duration / timescale))
            offset += length
        return out
    except struct.error as e:
        raise BadSidx(str(e)) from e


def _uri(video_id: str, fmt: Format) -> str:
    return f"/api/stream/{video_id}/{fmt.format_id}"


def media_playlist(video_id: str, fmt: Format, ranges: Ranges, segments: list[Segment]) -> str:
    """One track (video or audio) as a VOD playlist of byte-range fragments."""
    uri = _uri(video_id, fmt)
    target = max(1, math.ceil(max((s.seconds for s in segments), default=1)))
    lines = ["#EXTM3U", "#EXT-X-VERSION:6", f"#EXT-X-TARGETDURATION:{target}", "#EXT-X-MEDIA-SEQUENCE:0",
             "#EXT-X-PLAYLIST-TYPE:VOD", "#EXT-X-INDEPENDENT-SEGMENTS",
             f'#EXT-X-MAP:URI="{uri}",BYTERANGE="{ranges.init_end + 1}@0"']
    for s in segments:
        lines += [f"#EXTINF:{s.seconds:.3f},", f"#EXT-X-BYTERANGE:{s.size}@{s.start}", uri]
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


def master_playlist(video_id: str, video: list[Format], audio: Format, lang: str = "und", name: str = "Audio") -> str:
    """The entry point: one variant per resolution, all sharing the one audio track."""
    lines = ["#EXTM3U", "#EXT-X-VERSION:6", "#EXT-X-INDEPENDENT-SEGMENTS",
             f'#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="{name}",LANGUAGE="{lang}",DEFAULT=YES,AUTOSELECT=YES,'
             f'URI="/api/hls/{video_id}/{audio.format_id}.m3u8"']
    audio_kbps = audio.tbr or 128
    for f in sorted(video, key=lambda f: f.height or 0):
        bandwidth = int(((f.tbr or 1000) + audio_kbps) * 1000)
        attrs = [f"BANDWIDTH={bandwidth}", f"AVERAGE-BANDWIDTH={bandwidth}",
                 f'CODECS="{f.vcodec},{audio.acodec}"', f"RESOLUTION={f.width or 0}x{f.height or 0}"]
        if f.fps:
            attrs.append(f"FRAME-RATE={float(f.fps):.3f}")
        attrs.append('AUDIO="aud"')
        lines += ["#EXT-X-STREAM-INF:" + ",".join(attrs), f"/api/hls/{video_id}/{f.format_id}.m3u8"]
    return "\n".join(lines) + "\n"
