"""Build a DASH (MPEG-DASH on-demand) manifest for one video.

Each Representation points at our own /api/stream/<video>/<format> proxy URL, so the
browser's player (Shaka) never sees a googlevideo address.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass

from ..extract.base import Format
from .isobmff import Ranges

NS = "urn:mpeg:dash:schema:mpd:2011"


@dataclass
class Rep:
    fmt: Format
    ranges: Ranges


def select_formats(formats: list[Format]) -> tuple[list[Format], list[Format]]:
    """Pick MP4 H.264 video-only and AAC audio-only formats (what every browser's MSE accepts).
    One video format per height (highest bitrate wins). VP9/AV1/WebM is a later addition."""
    best: dict[int, Format] = {}
    for f in formats:
        if (f.has_video and not f.has_audio and f.ext == "mp4" and (f.vcodec or "").startswith("avc1")
                and f.height):
            cur = best.get(f.height)
            if cur is None or (f.tbr or 0) > (cur.tbr or 0):
                best[f.height] = f
    video = [best[h] for h in sorted(best)]
    audio = [f for f in formats if f.has_audio and not f.has_video and f.ext == "m4a"
             and (f.acodec or "").startswith("mp4a")]
    return video, sorted(audio, key=lambda f: (f.language or "", f.tbr or 0))


def _duration(seconds: float) -> str:
    return f"PT{seconds:.3f}S"


def build_mpd(video_id: str, duration: float, video: list[Rep], audio: list[Rep]) -> str:
    ET.register_namespace("", NS)
    mpd = ET.Element(f"{{{NS}}}MPD", {
        "type": "static", "mediaPresentationDuration": _duration(duration),
        "minBufferTime": "PT2S", "profiles": "urn:mpeg:dash:profile:isoff-on-demand:2011"})
    period = ET.SubElement(mpd, f"{{{NS}}}Period")

    def adaptation(kind: str, mime: str, reps: list[Rep], lang: str | None = None) -> None:
        a = ET.SubElement(period, f"{{{NS}}}AdaptationSet", {
            "contentType": kind, "mimeType": mime, "segmentAlignment": "true",
            "startWithSAP": "1", **({"lang": lang} if lang else {})})
        for r in reps:
            f = r.fmt
            attrs = {"id": f.format_id, "codecs": (f.vcodec if kind == "video" else f.acodec) or "",
                     "bandwidth": str(int((f.tbr or 1000) * 1000))}
            if kind == "video":
                attrs.update(width=str(f.width or 0), height=str(f.height or 0))
                if f.fps:
                    attrs["frameRate"] = str(int(f.fps))
            else:
                attrs["audioSamplingRate"] = "44100"
            rep = ET.SubElement(a, f"{{{NS}}}Representation", attrs)
            ET.SubElement(rep, f"{{{NS}}}BaseURL").text = f"/api/stream/{video_id}/{f.format_id}"
            sb = ET.SubElement(rep, f"{{{NS}}}SegmentBase",
                               {"indexRange": f"{r.ranges.index_start}-{r.ranges.index_end}"})
            ET.SubElement(sb, f"{{{NS}}}Initialization", {"range": f"0-{r.ranges.init_end}"})

    if video:
        adaptation("video", "video/mp4", video)
    langs: dict[str, list[Rep]] = {}
    for r in audio:                       # one AdaptationSet per language (dubbed audio tracks)
        langs.setdefault(r.fmt.language or "und", []).append(r)
    for lang, reps in langs.items():
        adaptation("audio", "audio/mp4", reps, lang)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(mpd, encoding="unicode")
