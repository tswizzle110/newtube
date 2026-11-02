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


# ---- the same parsing against REAL entries (tests/fixtures_entries.py) ----------------------

from tests import fixtures_entries as real  # noqa: E402


def test_real_search_video_entry():
    v = parse_entry(real.SEARCH_VIDEO)
    assert isinstance(v, VideoSummary)
    assert (v.id, v.title, v.duration, v.view_count) == ("jNQXAC9IVRw", "Me at the zoo", 19, 447482126)
    assert (v.channel_id, v.channel_title, v.published_at, v.is_live) == ("UC4QobU6STFB0P71PMvOGN5A", "jawed", None, False)


def test_real_search_channel_entry_avatar_is_made_absolute_https():
    c = parse_entry(real.SEARCH_CHANNEL)
    assert isinstance(c, ChannelSummary)
    assert (c.id, c.title, c.handle, c.subscriber_count) == ("UCBR8-60-B28hp2BmDPdntcQ", "YouTube", "@YouTube", 46600000)
    assert c.avatar_url.startswith("https://yt3.ggpht.com/") and c.avatar_url.endswith("s176-c-k-c0x00ffffff-no-rj-mo")
    from newtube.net.http import check_url
    check_url(c.avatar_url)          # would raise for the original "//yt3..." form


def test_real_search_playlist_entry():
    p = parse_entry(real.SEARCH_PLAYLIST)
    assert isinstance(p, PlaylistSummary)
    assert (p.id, p.title, p.channel_title, p.video_count) == ("PLBDA2E52FB1EF80C9", "World History", "CrashCourse", None)
    assert p.thumbnail_url.startswith("https://i.ytimg.com/vi/Yocja_N5s1I/")


def test_real_channel_tab_entry_has_no_channel_or_views():
    v = parse_entry(real.CHANNEL_TAB_VIDEO)
    assert isinstance(v, VideoSummary) and v.duration == 607
    assert (v.channel_id, v.channel_title, v.view_count) == (None, None, None)   # caller must fill these in


def test_real_playlist_video_entry():
    v = parse_entry(real.PLAYLIST_VIDEO)
    assert (v.id, v.channel_id, v.view_count, v.duration) == ("Yocja_N5s1I", "UCX6b17PVsYBQ0ip5gyeme-Q", 17000000, 671)


def test_channel_info_avatar_is_absolute():
    c = parse_channel_info({"channel_id": UC, "channel": "C", "thumbnails": [
        {"id": "avatar_uncropped", "url": "//yt3.ggpht.com/x=s0"}]})
    assert c.avatar_url == "https://yt3.ggpht.com/x=s0"


# ---- comments. SYNTHETIC dicts: field names taken from yt-dlp's source (`_extract_comment`);
# ---- `selftest --dump` writes real ones to replace these. ----------------------------------

from newtube.extract.ytdlp import parse_comment, parse_comments  # noqa: E402

TOP = {"id": "Ugx1", "parent": "root", "text": "first!", "author": "Ann", "author_id": UC,
       "author_thumbnail": "//yt3.ggpht.com/a=s48", "like_count": 1200, "author_is_uploader": False,
       "is_favorited": True, "is_pinned": True, "_time_text": "2 days ago", "timestamp": 1700000000}
REPLY = {"id": "Ugx1.r1", "parent": "Ugx1", "text": "reply", "author": "Bob", "like_count": 3}


def test_parse_comment_fields_and_root_parent():
    c = parse_comment(TOP)
    assert c.parent is None and (c.text, c.author, c.like_count) == ("first!", "Ann", 1200)
    assert (c.is_pinned, c.is_hearted, c.is_uploader) == (True, True, False)
    assert c.time_text == "2 days ago" and c.timestamp == 1700000000
    assert c.author_thumbnail == "https://yt3.ggpht.com/a=s48"        # made absolute
    r = parse_comment(REPLY)
    assert r.parent == "Ugx1" and r.timestamp is None and not r.is_pinned


def test_parse_comments_skips_junk_and_dupes():
    info = {"comments": [TOP, REPLY, dict(TOP), {"id": "x"}, {"text": "no id"}, None, "junk",
                         {"id": "Ugx9", "text": 5}]}
    assert [c.id for c in parse_comments(info)] == ["Ugx1", "Ugx1.r1"]
    assert parse_comments({}) == [] and parse_comments({"comments": None}) == []


async def test_comments_request_builds_yt_dlp_limits(monkeypatch):
    from newtube.config import Settings
    from newtube.extract.ytdlp import YtDlpExtractor
    from newtube.net.ratelimit import RateLimiter
    ex = YtDlpExtractor(Settings(min_request_interval=0.7), RateLimiter(0, 0, 10))
    seen = {}

    async def fake(url, extra, background):
        seen.update(url=url, **extra)
        return {"comments": [TOP, REPLY], "comment_count": 4321, "formats": ["huge"]}
    monkeypatch.setattr(ex, "_extract", fake)

    page = await ex.comments("jNQXAC9IVRw", "new", 20)             # top level only
    args = seen["extractor_args"]["youtube"]
    assert args["max_comments"] == ["20", "20", "all", "0", "1"] and args["comment_sort"] == ["new"]
    assert seen["getcomments"] is True and seen["sleep_interval_requests"] == 0.7
    assert page.total_estimate == 4321 and len(page.comments) == 2
    await ex.comments("jNQXAC9IVRw", "top", 20, replies_for=3, replies_per_thread=5)
    assert seen["extractor_args"]["youtube"]["max_comments"] == ["18", "3", "all", "5", "2"]
    for bad in [dict(sort="hot"), dict(limit=0), dict(limit=101), dict(replies_for=16), dict(replies_per_thread=0)]:
        with pytest.raises(ExtractionError):
            await ex.comments("jNQXAC9IVRw", **bad)
    with pytest.raises(ExtractionError):
        await ex.comments("bad id")


async def test_channel_sort_urls_and_validation(monkeypatch):
    from newtube.config import Settings
    from newtube.extract.ytdlp import YtDlpExtractor
    from newtube.net.ratelimit import RateLimiter
    ex = YtDlpExtractor(Settings(), RateLimiter(0, 0, 10))
    urls = []

    async def fake(url, extra, background):
        urls.append((url, extra["playlistend"]))
        return {"entries": []}
    monkeypatch.setattr(ex, "_extract", fake)
    for sort in ("newest", "popular", "oldest"):
        await ex.raw_channel_videos(UC, 40, sort=sort)
    base = f"https://www.youtube.com/channel/{UC}/videos"
    assert urls == [(base, 40), (base + "?view=0&sort=p&flow=grid", 40), (base + "?view=0&sort=da&flow=grid", 40)]
    await ex.raw_channel_videos(UC, 9999)
    assert urls[-1][1] == 150                                              # clamped
    with pytest.raises(ExtractionError):
        await ex.raw_channel_videos(UC, 10, sort="random")
