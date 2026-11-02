"""A SYNTHETIC Google Takeout, built from what is known of the format (the real export has not been seen).
`newtube takeout inspect` on a real export tells us where reality differs; adjust here when it does."""
import csv
import io
import json
import zipfile
from pathlib import Path

UC1, UC2, UC3 = "UC" + "a" * 22, "UC" + "b" * 22, "UC" + "c" * 22


def watch(vid, time, title="Some video", channel=("Chan A", UC1), header="YouTube", url=None, details=None, extra=None):
    e = {"header": header, "title": f"Watched {title}", "titleUrl": url or f"https://www.youtube.com/watch?v={vid}",
         "time": time, "products": [header], "activityControls": ["YouTube watch history"]}
    if channel:
        e["subtitles"] = [{"name": channel[0], "url": f"https://www.youtube.com/channel/{channel[1]}"}]
    if details:
        e["details"] = details
    e.update(extra or {})
    return e


WATCH_HISTORY = [
    watch("aaaaaaaaaaa", "2026-10-30T12:00:00.000Z", "Newest watch"),                                   # kept
    watch("bbbbbbbbbbb", "2026-10-29T10:00:00Z", "Second video", ("Chan B", UC2)),                       # kept
    watch("aaaaaaaaaaa", "2026-10-01T09:30:00.123Z", "Newest watch"),                                    # a REWATCH of the first: kept
    {"header": "YouTube", "title": "Watched a video that has been removed", "time": "2026-09-01T00:00:00Z",
     "products": ["YouTube"]},                                                                           # skipped: removed
    watch("ccccccccccc", "2026-09-02T00:00:00Z", "An ad", details=[{"name": "From Google Ads"}]),        # skipped: ad
    {"header": "YouTube", "title": "Viewed Some community post", "titleUrl": "https://www.youtube.com/post/UgkxABC",
     "time": "2026-09-03T00:00:00Z", "products": ["YouTube"]},                                           # skipped: post
    {"header": "YouTube", "title": "Watched https://www.youtube.com/watch?v=ddddddddddd",
     "titleUrl": "https://www.youtube.com/watch?v=ddddddddddd", "time": "2026-09-04T00:00:00Z"},        # skipped: deleted/private
    watch("eeeeeeeeeee", "2026-09-05T00:00:00Z", "A song", ("Artist", UC3), header="YouTube Music",
          url="https://music.youtube.com/watch?v=eeeeeeeeeee"),                                          # skipped: music (default)
    watch("fffffffffff", "2026-09-06T00:00:00Z", "A short", url="https://www.youtube.com/shorts/fffffffffff"),   # kept: a shorts url
    watch("ggggggggggg", "2026-09-07T00:00:00Z", "No channel known", channel=None),                      # kept, channel unknown
    watch("hhhhhhhhhhh", "2026-09-08T00:00:00Z", "Short link", url="https://youtu.be/hhhhhhhhhhh"),      # kept
    {**watch("iiiiiiiiiii", "x", "No time"), "time": None},                                              # skipped: no time
    {"header": "Google Ads", "title": "Watched thing", "titleUrl": "https://www.youtube.com/watch?v=jjjjjjjjjjj",
     "time": "2026-09-09T00:00:00Z"},                                                                    # skipped: other product
    {"header": "YouTube", "title": "Visited a channel", "titleUrl": "https://www.youtube.com/channel/" + UC2,
     "time": "2026-09-10T00:00:00Z"},                                                                    # skipped: other link
    watch("kkkkkkkkkkk", "2026-09-11T00:00:00Z", "Quote \"marks\", {braces} and ] brackets é中"),   # kept: nasty characters
    "not even an object",                                                                                # skipped: malformed (kept here for the parser tests; the files omit it: a non-object makes the JSON reader report damage)
]
# (kept: aaaaaaaaaaa x2, bbbbbbbbbbb, fffffffffff, ggggggggggg, hhhhhhhhhhh, kkkkkkkkkkk = 7 watches, 6 different videos)
KEPT_WATCHES, KEPT_VIDEOS = 7, 6

SEARCH_HISTORY = [
    {"header": "YouTube", "title": "Searched for cats playing piano",
     "titleUrl": "https://www.youtube.com/results?search_query=cats+playing+piano", "time": "2026-10-20T08:00:00Z", "products": ["YouTube"]},
    {"header": "YouTube", "title": "Searched for café 中", "titleUrl": "https://www.youtube.com/results?search_query=caf%C3%A9+%E4%B8%AD",
     "time": "2026-10-19T08:00:00Z"},
    {"header": "YouTube", "title": "Searched for cats playing piano",
     "titleUrl": "https://www.youtube.com/results?search_query=cats+playing+piano", "time": "2026-10-18T08:00:00Z"},   # same query, another day
]

SUBSCRIPTIONS = [["Channel ID", "Channel URL", "Channel title"],
                 [UC1, f"http://www.youtube.com/channel/{UC1}", "Chan A"],
                 [UC2, f"http://www.youtube.com/channel/{UC2}", "Chan B, the \"second\" one"],
                 [UC3, f"http://www.youtube.com/channel/{UC3}", "Chan C"]]

PLAYLISTS_INDEX = [["Playlist ID", "Playlist create timestamp", "Playlist update timestamp", "Playlist title (original)", "Playlist title (current)", "Playlist visibility"],
                   ["LL", "2020-01-01T00:00:00+00:00", "2026-10-01T00:00:00+00:00", "Liked videos", "Liked videos", "Private"],
                   ["WL", "2020-01-01T00:00:00+00:00", "2026-10-01T00:00:00+00:00", "Watch later", "Watch later", "Private"],
                   ["PLabcdefghijklmnop", "2021-05-05T00:00:00+00:00", "2021-05-06T00:00:00+00:00", "My mix", "My mix", "Public"]]

LIKES = [["Video ID", "Playlist video creation timestamp"], ["aaaaaaaaaaa", "2026-08-01T10:00:00+00:00"], ["lllllllllll", "2026-07-01T10:00:00+00:00"]]
LATER = [["Video ID", "Playlist video creation timestamp"], ["mmmmmmmmmmm", "2026-06-01T10:00:00+00:00"]]
MIX = [["Video ID", "Playlist video creation timestamp"], ["nnnnnnnnnnn", "2021-05-05T10:00:00+00:00"], ["aaaaaaaaaaa", "2021-05-06T10:00:00+00:00"]]

ROOT = "Takeout/YouTube and YouTube Music/"


def csv_text(rows):
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\r\n").writerows(rows)
    return buf.getvalue()


def files(watch_history=None, language="en"):
    names = {"en": ("history/watch-history.json", "history/search-history.json", "subscriptions/subscriptions.csv",
                    "playlists/playlists.csv", "playlists/Liked videos-videos.csv", "playlists/Watch later-videos.csv", "playlists/My mix-videos.csv"),
             # a Spanish export: localised folder + file names, and localised CSV headers
             "es": ("historial/historial de reproducciones.json", "historial/historial de búsquedas.json", "suscripciones/suscripciones.csv",
                    "listas de reproducción/listas de reproducción.csv", "listas de reproducción/Vídeos que me gustan-videos.csv",
                    "listas de reproducción/Ver más tarde-videos.csv", "listas de reproducción/Mi mezcla-videos.csv")}[language]
    subs, idx, likes, later, mix = SUBSCRIPTIONS, PLAYLISTS_INDEX, LIKES, LATER, MIX
    if language == "es":
        subs = [["ID del canal", "URL del canal", "Título del canal"]] + SUBSCRIPTIONS[1:]
        likes, later, mix = ([["ID del vídeo", "Marca de tiempo de creación del vídeo de la lista"]] + x[1:] for x in (LIKES, LATER, MIX))
    data = {
        names[0]: json.dumps([e for e in (WATCH_HISTORY if watch_history is None else watch_history) if isinstance(e, dict)], indent=4),
        names[1]: json.dumps(SEARCH_HISTORY, indent=4), names[2]: csv_text(subs), names[3]: csv_text(idx),
        names[4]: csv_text(likes), names[5]: csv_text(later), names[6]: csv_text(mix),
        "Takeout/archive_browser.html": "<html></html>", "Takeout/YouTube and YouTube Music/videos/clip.mp4": "xx",
        ROOT + "profile/profile.json": json.dumps({"name": "x", "customUrl": "y"}),
        ROOT + "music/library.csv": csv_text([["Song", "Artist"], ["a", "b"]]),
    }
    return {(ROOT + k if not k.startswith("Takeout/") else k): v for k, v in data.items()}


def make_zip(path: Path, **kw) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files(**kw).items():
            z.writestr(name, text)
    return path


def make_folder(path: Path, **kw) -> Path:
    for name, text in files(**kw).items():
        f = path / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    return path
