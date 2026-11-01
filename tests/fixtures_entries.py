"""Real yt-dlp flat-list entries, copied from `newtube selftest --dump` output (2026-11-01).
URL query strings were redacted by the dump; a few trailing keys were cut off and are omitted."""

SEARCH_VIDEO = {
    "_type": "url", "ie_key": "Youtube", "id": "jNQXAC9IVRw", "title": "Me at the zoo",
    "url": "https://www.youtube.com/watch?REDACTED",
    "description": "Microplastics are accumulating in human brains at an alarming rate ...",
    "duration": 19, "channel_id": "UC4QobU6STFB0P71PMvOGN5A", "channel": "jawed",
    "channel_url": "https://www.youtube.com/channel/UC4QobU6STFB0P71PMvOGN5A",
    "uploader": "jawed", "uploader_id": "@jawed", "uploader_url": "https://www.youtube.com/@jawed",
    "thumbnails": [{"url": "https://i.ytimg.com/vi/jNQXAC9IVRw/hqdefault.jpg?REDACTED", "height": 270, "width": 480}],
    "timestamp": None, "release_timestamp": None, "availability": None, "view_count": 447482126,
    "live_status": None, "channel_is_verified": True,
}

SEARCH_CHANNEL = {
    "_type": "url", "url": "https://www.youtube.com/channel/UCBR8-60-B28hp2BmDPdntcQ",
    "id": "UCBR8-60-B28hp2BmDPdntcQ", "ie_key": "YoutubeTab", "channel": "YouTube", "uploader": "YouTube",
    "channel_id": "UCBR8-60-B28hp2BmDPdntcQ", "title": "YouTube", "uploader_id": "@YouTube",
    "channel_follower_count": 46600000,
    # NOTE: protocol-relative, as returned by YouTube
    "thumbnails": [
        {"url": "//yt3.ggpht.com/3s6evpqAiDU9tQR4sC2siJippbH2RWVPnwHgyl4V0th2iuQz0VDQZbUhQBGmsxLYo-mjG6TqZQ=s88-c-k-c0x00ffffff-no-rj-mo", "height": 88, "width": 88},
        {"url": "//yt3.ggpht.com/3s6evpqAiDU9tQR4sC2siJippbH2RWVPnwHgyl4V0th2iuQz0VDQZbUhQBGmsxLYo-mjG6TqZQ=s176-c-k-c0x00ffffff-no-rj-mo", "height": 176, "width": 176}],
    "playlist_count": None, "description": "YouTube's official YouTube channel.", "channel_is_verified": True,
}

SEARCH_PLAYLIST = {
    "title": "World History",
    "thumbnails": [{"url": "https://i.ytimg.com/vi/Yocja_N5s1I/hq720.jpg?REDACTED", "height": 202, "width": 360},
                   {"url": "https://i.ytimg.com/vi/Yocja_N5s1I/hq720.jpg?REDACTED", "height": 404, "width": 720}],
    "duration": None, "view_count": None, "timestamp": None, "live_status": None, "availability": None,
    "channel_url": "https://www.youtube.com/channel/UCX6b17PVsYBQ0ip5gyeme-Q",
    "channel": "CrashCourse", "channel_id": "UCX6b17PVsYBQ0ip5gyeme-Q", "uploader": "CrashCourse",
    "uploader_id": "@crashcourse", "ie_key": "YoutubeTab", "id": "PLBDA2E52FB1EF80C9", "_type": "url",
    "url": "https://www.youtube.com/playlist?REDACTED",
}

# A channel's /videos tab: NO channel, view count or date on the entries.
CHANNEL_TAB_VIDEO = {
    "title": "Daddy Yankee reacts to his Watch History",
    "thumbnails": [{"url": "https://i.ytimg.com/vi/Yil32mThG04/hq720_custom_3.jpg?REDACTED", "height": 202, "width": 360}],
    "duration": 607, "view_count": None, "timestamp": None, "live_status": None, "availability": None,
    "channel_url": None, "uploader_url": None, "creators": None, "ie_key": "Youtube", "id": "Yil32mThG04",
    "_type": "url", "url": "https://www.youtube.com/watch?REDACTED",
}

PLAYLIST_VIDEO = {
    "title": "The Agricultural Revolution: Crash Course World History #1",
    "thumbnails": [{"url": "https://i.ytimg.com/vi/Yocja_N5s1I/hqdefault.jpg?REDACTED", "height": 188, "width": 336}],
    "duration": 671, "view_count": 17000000, "timestamp": None, "live_status": None, "availability": None,
    "channel_url": "https://www.youtube.com/channel/UCX6b17PVsYBQ0ip5gyeme-Q",
    "channel": "CrashCourse", "channel_id": "UCX6b17PVsYBQ0ip5gyeme-Q", "uploader": "CrashCourse",
    "uploader_id": "@crashcourse", "ie_key": "Youtube", "id": "Yocja_N5s1I", "_type": "url",
}
