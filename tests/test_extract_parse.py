"""Parsing tests use a synthetic yt-dlp info dict shaped like real output.

`newtube selftest --dump DIR` (next commit) saves real info dicts from your
server; drop one in tests/fixtures/ to extend these against real data."""

import pytest

from newtube.extract.base import AgeRestricted, BotCheck, ExtractionError, VideoUnavailable
from newtube.extract.ytdlp import (
    classify_error, normalize_channel_ref, parse_channel_info, parse_video_info,
)

UC = "UCXuqSBlHAE6Xw-yeJA0Tunw"

INFO = {
    "id": "dQw4w9WgXcQ", "title": "T", "description": "d", "duration": 213.0,
    "channel_id": UC, "channel": "Chan", "timestamp": 1256000000, "view_count": 5,
    "categories": ["Music"], "tags": ["a", "b"], "thumbnail": "https://i.ytimg.com/x.jpg",
    "live_status": "not_live", "age_limit": 0, "language": "en",
    "chapters": [{"start_time": 0, "end_time": 10, "title": "Intro"},
                 {"start_time": 10.5, "end_time": 20, "title": "Main"}],
    "formats": [
        {"format_id": "sb0", "url": "https://i.ytimg.com/sb.mhtml", "ext": "mhtml",
         "vcodec": "none", "acodec": "none", "protocol": "mhtml"},
        {"format_id": "18", "url": "https://rr1.googlevideo.com/a", "ext": "mp4",
         "vcodec": "avc1.42001E", "acodec": "mp4a.40.2", "height": 360, "protocol": "https"},
        {"format_id": "137", "url": "https://rr1.googlevideo.com/b", "ext": "mp4",
         "vcodec": "avc1.640028", "acodec": "none", "height": 1080, "protocol": "https"},
        {"format_id": "140", "url": "https://rr1.googlevideo.com/c", "ext": "m4a",
         "vcodec": "none", "acodec": "mp4a.40.2", "protocol": "https"},
        {"format_id": "91", "url": "https://manifest.googlevideo.com/x.m3u8", "ext": "mp4",
         "vcodec": "avc1", "acodec": "mp4a", "protocol": "m3u8_native"},
    ],
    "subtitles": {"en": [{"ext": "srv3", "url": "https://www.youtube.com/s3"},
                         {"ext": "vtt", "url": "https://www.youtube.com/s1", "name": "English"}]},
    "automatic_captions": {"en-orig": [{"ext": "vtt", "url": "https://www.youtube.com/a1"}],
                           "fr": [{"ext": "vtt", "url": "https://www.youtube.com/a2"}]},
}


def test_parse_video_info():
    v = parse_video_info(INFO, now=7)
    assert (v.id, v.duration, v.published_at, v.category, v.fetched_at) == (
        "dQw4w9WgXcQ", 213, 1256000000, "Music", 7)
    assert [(c.start, c.title) for c in v.chapters] == [(0.0, "Intro"), (10.5, "Main")]
    assert not v.is_live


def test_formats_filter_and_classify():
    v = parse_video_info(INFO)
    ids = {f.format_id: f for f in v.formats}
    assert set(ids) == {"18", "137", "140"}      # storyboard + m3u8 dropped
    assert ids["18"].is_muxed
    assert ids["137"].has_video and not ids["137"].has_audio
    assert ids["140"].has_audio and not ids["140"].has_video


def test_captions_prefer_vtt_and_drop_machine_translations():
    caps = parse_video_info(INFO).captions
    assert [(c.lang, c.ext, c.auto) for c in caps] == [("en", "vtt", False), ("en-orig", "vtt", True)]


def test_upload_date_fallback():
    v = parse_video_info({"id": "x" * 11, "upload_date": "20240102"})
    assert v.published_at == 1704153600


@pytest.mark.parametrize("msg,exc", [
    ("Sign in to confirm you’re not a bot", BotCheck),
    ("Sign in to confirm your age. This video may be inappropriate", AgeRestricted),
    ("Private video. Sign in if you've been granted access", VideoUnavailable),
    ("Video unavailable", VideoUnavailable),
    ("something odd", ExtractionError),
])
def test_classify_error(msg, exc):
    assert type(classify_error(msg)) is exc


@pytest.mark.parametrize("ref,url", [
    (UC, f"https://www.youtube.com/channel/{UC}/videos"),
    ("@mkbhd", "https://www.youtube.com/@mkbhd/videos"),
    ("https://www.youtube.com/@mkbhd/featured", "https://www.youtube.com/@mkbhd/videos"),
    (f"youtube.com/channel/{UC}", f"https://www.youtube.com/channel/{UC}/videos"),
])
def test_normalize_channel_ref(ref, url):
    assert normalize_channel_ref(ref) == url


@pytest.mark.parametrize("ref", ["https://evil.com/@x", "file:///etc/passwd", "hello world"])
def test_normalize_rejects_foreign_input(ref):
    with pytest.raises(ExtractionError):
        normalize_channel_ref(ref)


def test_parse_channel_info():
    c = parse_channel_info({"channel_id": UC, "channel": "Chan", "uploader_id": "@chan",
                            "thumbnails": [{"id": "avatar_uncropped", "url": "https://yt3.ggpht.com/a"}]})
    assert (c.id, c.title, c.handle, c.avatar_url) == (UC, "Chan", "@chan", "https://yt3.ggpht.com/a")


def test_formats_keep_language_preference_for_original_audio_detection():
    from newtube.extract.ytdlp import parse_formats
    fm = parse_formats([{"format_id": "140-20", "url": "https://x.googlevideo.com/a", "ext": "m4a",
                         "vcodec": "none", "acodec": "mp4a.40.2", "protocol": "https", "language": "en-US",
                         "language_preference": 10, "format_note": "English (US) original (default), medium"}])
    assert fm[0].language_preference == 10 and fm[0].language == "en-US"


# ---- list entries. SYNTHETIC samples shaped like yt-dlp's flat output; `selftest --dump`
# ---- writes the real thing, to be swapped in as fixtures. --------------------------------

from newtube.extract.base import ChannelSummary, PlaylistSummary, VideoSummary  # noqa: E402
from newtube.extract.ytdlp import parse_entries, parse_entry  # noqa: E402


def test_parse_entry_classifies_by_id_shape():
    v = parse_entry({"id": "jNQXAC9IVRw", "title": "Zoo", "duration": 19.0, "view_count": 5,
                     "channel_id": UC, "channel": "jawed", "live_status": "not_live"})
    assert isinstance(v, VideoSummary) and (v.duration, v.channel_id, v.is_live) == (19, UC, False)
    c = parse_entry({"id": UC, "title": "YouTube", "url": "https://www.youtube.com/@YouTube",
                     "channel_follower_count": 1000, "thumbnails": [{"url": "https://yt3.ggpht.com/a"}]})
    assert isinstance(c, ChannelSummary) and c.handle == "@YouTube" and c.avatar_url.endswith("/a")
    pl = parse_entry({"id": "PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI", "title": "List", "channel": "Crash",
                      "playlist_count": 40})
    assert isinstance(pl, PlaylistSummary) and pl.video_count == 40


def test_parse_entry_ignores_unknown_shapes_and_live():
    assert parse_entry({"id": "short", "title": "x"}) is None
    assert parse_entry({"title": "no id"}) is None
    assert parse_entry({"id": "jNQXAC9IVRw", "title": "t", "live_status": "is_live"}).is_live


def test_parse_entries_dedupes_and_mixes_kinds():
    info = {"entries": [{"id": "jNQXAC9IVRw", "title": "a"}, {"id": "jNQXAC9IVRw", "title": "dup"},
                        {"id": UC, "title": "chan"}, None, "junk", {"id": "x", "title": "?"}]}
    assert [type(i).__name__ for i in parse_entries(info)] == ["VideoSummary", "ChannelSummary"]


async def test_search_builds_filtered_url_and_validates(monkeypatch):
    from newtube.config import Settings
    from newtube.extract.ytdlp import YtDlpExtractor
    from newtube.net.ratelimit import RateLimiter
    ex = YtDlpExtractor(Settings(), RateLimiter(0, 0, 10))
    seen = {}

    async def fake(url, extra, background):
        seen.update(url=url, extra=extra)
        return {"entries": [{"id": "jNQXAC9IVRw", "title": "Zoo"}]}
    monkeypatch.setattr(ex, "_extract", fake)
    res = await ex.search("me at the zoo & more", "channel", limit=500)
    assert "search_query=me+at+the+zoo+%26+more" in seen["url"] and seen["url"].endswith("sp=EgIQAg%3D%3D")
    assert seen["extra"]["playlistend"] == 50           # clamped
    assert res[0].id == "jNQXAC9IVRw"
    for bad in [("", "video"), ("x" * 201, "video"), ("ok", "everything")]:
        with pytest.raises(ExtractionError):
            await ex.search(*bad)
    with pytest.raises(ExtractionError):
        await ex.raw_channel_videos("not-a-channel")
    with pytest.raises(ExtractionError):
        await ex.raw_playlist("../etc/passwd")
