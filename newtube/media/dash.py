"""Build a DASH (MPEG-DASH on-demand) manifest for one video.

Each Representation points at our own /api/stream/<video>/<format> proxy URL, so the
browser's player (Shaka) never sees a googlevideo address.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from ..extract.base import Format
from .isobmff import Ranges

NS = "urn:mpeg:dash:schema:mpd:2011"


@dataclass
class Rep:
    fmt: Format
    ranges: Ranges


@dataclass(frozen=True)
class AudioTrack:
    lang: str
    name: str
    original: bool


def _audio_candidates(formats: list[Format]) -> list[Format]:
    return [f for f in formats if f.has_audio and not f.has_video and f.ext == "m4a"
            and (f.acodec or "").startswith("mp4a") and not f.is_drc]


def _original_audio(cands: list[Format]) -> list[Format]:
    """The video's own audio. yt-dlp marks it with the highest language_preference; when a
    video has a single track (no preferences differ) every candidate is the original."""
    prefs = {f.language_preference for f in cands if f.language_preference is not None}
    if len(prefs) > 1:
        top = max(prefs)
        return [f for f in cands if f.language_preference == top]
    return cands


def _track_name(f: Format) -> str:
    first = (f.note or "").split(",")[0]
    name = re.sub(r"\s*(original\s*)?\(default\)", "", first).strip()
    return name or f.language or "Unknown"


def audio_tracks(formats: list[Format]) -> list[AudioTrack]:
    """Selectable audio languages. Empty when the video has only one (nothing to choose)."""
    cands = _audio_candidates(formats)
    orig_langs = {f.language for f in _original_audio(cands)}
    seen: dict[str, AudioTrack] = {}
    for f in cands:
        lang = f.language or "und"
        if lang not in seen:
            seen[lang] = AudioTrack(lang, _track_name(f), f.language in orig_langs)
    if len(seen) < 2:
        return []
    return sorted(seen.values(), key=lambda a: (not a.original, a.name))


FAMILY_ORDER = ("avc1", "vp9", "av1")      # tie-break at equal quality: most compatible / cheapest to decode first
CODECS = ("auto",) + FAMILY_ORDER


def codec_family(f: Format) -> str | None:
    """'avc1' (H.264 in MP4), 'vp9' (WebM) or 'av1' (MP4): the combinations YouTube serves and
    browsers play through MSE. Anything else (e.g. AV1 in WebM) is ignored."""
    v = f.vcodec or ""
    if v.startswith("avc1") and f.ext == "mp4":
        return "avc1"
    if (v.startswith("vp09") or v == "vp9") and f.ext == "webm":
        return "vp9"
    if v.startswith("av01") and f.ext == "mp4":
        return "av1"
    return None


def video_ladders(formats: list[Format]) -> dict[str, dict[int, Format]]:
    """{family: {height: best format at that height}}: SDR video-only streams, highest bitrate
    wins per height. HDR is left out (it looks washed out on a normal screen)."""
    out: dict[str, dict[int, Format]] = {}
    for f in formats:
        fam = codec_family(f)
        if not fam or not f.has_video or f.has_audio or not f.height:
            continue
        if f.dynamic_range not in (None, "SDR") or (f.vcodec or "").startswith("vp09.02"):
            continue
        rungs = out.setdefault(fam, {})
        cur = rungs.get(f.height)
        if cur is None or (f.tbr or 0) > (cur.tbr or 0):
            rungs[f.height] = f
    return out


def choose_family(ladders: dict[str, dict[int, Format]], codec: str = "auto", max_height: int = 0) -> str | None:
    """Which codec family to offer. A requested family wins if the video has it; 'auto' takes the
    family that reaches the highest resolution (ties: H.264, then VP9, then AV1)."""
    def best(fam: str) -> int:
        return max((h for h in ladders[fam] if not max_height or h <= max_height), default=0)

    usable = [fam for fam in FAMILY_ORDER if fam in ladders and best(fam) > 0]
    if not usable:
        return None
    if codec in usable:
        return codec
    return max(usable, key=lambda fam: (best(fam), -FAMILY_ORDER.index(fam)))


def select_formats(formats: list[Format], audio_lang: str | None = None, codec: str = "auto",
                   max_height: int = 0) -> tuple[list[Format], list[Format]]:
    """Pick one codec family of video (every resolution it offers, up to ``max_height`` if set) and
    exactly ONE audio format: the best AAC stream of the requested language, else of the video's
    original audio. One family keeps quality switching seamless; one audio stream keeps the player
    from picking a dub and avoids probing dozens of streams."""
    ladders = video_ladders(formats)
    fam = choose_family(ladders, codec, max_height)
    video = [f for h, f in sorted(ladders[fam].items()) if not max_height or h <= max_height] if fam else []
    cands = _audio_candidates(formats)
    chosen = [f for f in cands if audio_lang and f.language == audio_lang] or _original_audio(cands)
    audio = [max(chosen, key=lambda f: f.tbr or 0)] if chosen else []
    return video, audio


def available_summary(formats: list[Format]) -> dict[str, int]:
    """{family: highest SDR height}: what a video offers (for selftest and the quality menu)."""
    return {fam: max(rungs) for fam, rungs in video_ladders(formats).items()}


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
        mime = "video/webm" if video[0].fmt.ext == "webm" else "video/mp4"   # one family -> one container
        adaptation("video", mime, video)
    langs: dict[str, list[Rep]] = {}
    for r in audio:                       # one AdaptationSet per language (dubbed audio tracks)
        langs.setdefault(r.fmt.language or "und", []).append(r)
    for lang, reps in langs.items():
        adaptation("audio", "audio/mp4", reps, lang)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(mpd, encoding="unicode")
