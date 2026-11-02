"""Pure parsing: streaming JSON, URLs, history entries, CSV recognition."""
import io
import json

import pytest

from newtube.importers.jsonstream import NotAnArrayOfObjects, iter_json_array
from newtube.importers.parse import (
    SearchEvent, WatchEvent, channel_id_from_url, classify_playlist_name, parse_activity, parse_playlist_index,
    parse_playlist_items, parse_subscriptions, parse_time, search_query, sniff_csv, video_id_from_url,
)
from tests import fixtures_takeout as fx

# ---- streaming JSON ---------------------------------------------------------------------------------

PAYLOAD = json.dumps(fx.WATCH_HISTORY[:-1], indent=2, ensure_ascii=False)       # (the last entry is a bare string)


@pytest.mark.parametrize("chunk", [1, 2, 3, 7, 64, 1 << 16])
def test_streaming_matches_json_load_at_any_chunk_size(chunk):
    assert list(iter_json_array(io.StringIO(PAYLOAD), chunk)) == json.loads(PAYLOAD)


def test_streaming_handles_bom_whitespace_empty_and_nasty_strings():
    assert list(iter_json_array(io.StringIO('﻿ \n[\n]\n'))) == []
    tricky = [{"t": 'closing } brace, "quote", ] bracket and \\ backslash', "n": 1}, {"t": "x"}]
    assert list(iter_json_array(io.StringIO(json.dumps(tricky)), 3)) == tricky


def test_streaming_rejects_what_is_not_a_list_of_objects_and_truncated_files():
    for bad in ('{"a": 1}', "[1, 2, 3]", "hello", '["x"]'):
        with pytest.raises(NotAnArrayOfObjects):
            list(iter_json_array(io.StringIO(bad)))
    with pytest.raises(json.JSONDecodeError):
        list(iter_json_array(io.StringIO('[{"a": 1}, {"b": ')))                       # cut off mid-object
    for broken in ('[{"a": 1} {"b": 2}]', '[{"a": 1},, {"b": 2}]', '[{"a": 1}, {"b" 2}]', '[{"a": 1},]', '[{"a": 1}'):
        with pytest.raises(json.JSONDecodeError):
            list(iter_json_array(io.StringIO(broken), 4))                            # invalid JSON is not silently accepted


def test_streaming_does_not_hold_the_whole_file():
    big = "[" + ",".join(json.dumps({"i": i, "pad": "x" * 200}) for i in range(5000)) + "]"
    it = iter_json_array(io.StringIO(big), 4096)
    next(it)
    assert sum(1 for _ in it) == 4999


# ---- ids, URLs, times -----------------------------------------------------------------------------

@pytest.mark.parametrize("url,vid", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s&list=PLx", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/watch?feature=share&v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://music.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/live/dQw4w9WgXcQ?feature=share", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ?t=3", "dQw4w9WgXcQ"),
])
def test_video_ids_are_found_in_every_url_shape(url, vid):
    assert video_id_from_url(url) == vid


@pytest.mark.parametrize("url", [
    None, "", "https://www.youtube.com/watch?v=short", "https://www.youtube.com/watch?v=waytoolongforanid",
    "https://youtube.com.evil.com/watch?v=dQw4w9WgXcQ", "https://evil.com/watch?v=dQw4w9WgXcQ",
    "https://www.youtube.com/channel/" + fx.UC1, "https://www.youtube.com/playlist?list=PLabcdefghijklmnop", "javascript:alert(1)"])
def test_look_alike_and_non_video_urls_are_rejected(url):
    assert video_id_from_url(url) is None


def test_search_queries_are_decoded():
    assert search_query("https://www.youtube.com/results?search_query=cats+playing+piano") == "cats playing piano"
    assert search_query("https://www.youtube.com/results?search_query=caf%C3%A9+%E4%B8%AD") == "café 中"
    assert search_query("https://www.youtube.com/results?search_query=") is None
    assert search_query("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is None and search_query(None) is None


def test_times():
    assert parse_time("2026-10-30T12:00:00.000Z") == parse_time("2026-10-30T12:00:00Z") == 1793361600
    assert parse_time("2026-10-30T14:00:00+02:00") == 1793361600
    assert parse_time("2026-10-30T12:00:00") == 1793361600                         # no zone: UTC
    for bad in (None, "", "yesterday", 5, "2026-13-45T00:00:00Z"):
        assert parse_time(bad) is None


def test_channel_ids():
    assert channel_id_from_url(f"https://www.youtube.com/channel/{fx.UC1}") == fx.UC1
    assert channel_id_from_url("https://www.youtube.com/@handle") is None and channel_id_from_url(None) is None


# ---- history entries: every kind of junk has a reason ---------------------------------------------

def parsed(entry, **kw):
    return parse_activity(entry, **kw)


def test_each_junk_entry_is_dropped_for_a_stated_reason_and_the_rest_kept():
    results = [parsed(e) for e in fx.WATCH_HISTORY]
    reasons = [r if isinstance(r, str) else "KEPT" for r in results]
    assert reasons == ["KEPT", "KEPT", "KEPT", "removed", "ad", "post", "unavailable", "music", "KEPT", "KEPT", "KEPT",
                       "no_time", "other_product", "other_url", "KEPT", "malformed"]


def test_a_watch_event_carries_what_the_recommender_needs():
    ev = parsed(fx.WATCH_HISTORY[0])
    assert ev == WatchEvent("aaaaaaaaaaa", 1793361600, "Newest watch", fx.UC1, "Chan A")
    no_channel = parsed(fx.WATCH_HISTORY[9])
    assert (no_channel.channel_id, no_channel.channel_title, no_channel.title) == (None, None, "No channel known")
    assert parsed(fx.WATCH_HISTORY[10]).video_id == "hhhhhhhhhhh"                    # youtu.be
    assert parsed(fx.WATCH_HISTORY[8]).video_id == "fffffffffff"                     # /shorts/
    assert parsed(fx.WATCH_HISTORY[14]).title == 'Quote "marks", {braces} and ] brackets é中'


def test_searches_are_recognised_by_their_link_not_by_the_file():
    ev = parsed(fx.SEARCH_HISTORY[1])
    assert ev == SearchEvent("café 中", parse_time("2026-10-19T08:00:00Z"))


def test_music_is_only_counted_when_asked_for():
    song = fx.WATCH_HISTORY[7]
    assert parsed(song) == "music"
    ev = parsed(song, include_music=True)
    assert isinstance(ev, WatchEvent) and ev.video_id == "eeeeeeeeeee"
    assert parsed({**song, "titleUrl": "https://music.youtube.com/playlist?list=PLx"}, include_music=True) == "music"


def test_non_english_prefixes_are_left_alone_but_the_entry_is_still_usable():
    e = {**fx.WATCH_HISTORY[0], "title": "Has visto Un vídeo cualquiera"}
    assert parsed(e).title == "Has visto Un vídeo cualquiera"                   # not stripped; backfill replaces it later


# ---- CSV files are recognised by their data, whatever language the header is in ----------------------

def rows(table):
    return table[1:]


@pytest.mark.parametrize("table,kind", [
    (fx.SUBSCRIPTIONS, "subscriptions"), (fx.LIKES, "playlist_items"), (fx.MIX, "playlist_items"),
    (fx.PLAYLISTS_INDEX, "playlists"), ([["Song", "Artist"], ["a", "b"], ["c", "d"]], "unknown"), ([], "unknown"),
])
def test_sniffing_by_content(table, kind):
    assert sniff_csv(rows(table)) == kind


def test_sniffing_survives_a_localised_header_and_missing_header_and_stray_rows():
    spanish = [["ID del canal", "URL del canal", "Título del canal"]] + fx.SUBSCRIPTIONS[1:]
    assert sniff_csv(spanish[1:]) == "subscriptions"
    assert sniff_csv(fx.SUBSCRIPTIONS[1:] * 4 + [["", "", ""], ["junk"]]) == "subscriptions"    # blank rows and one stray row tolerated
    assert sniff_csv(fx.SUBSCRIPTIONS[1:] + [["junk"]]) == "unknown"                           # but 1 bad row in 4 is too many
    assert sniff_csv(fx.LIKES) == "unknown" and sniff_csv(fx.LIKES[1:]) == "playlist_items"       # header row alone would confuse: caller retries


def test_csv_parsers():
    assert parse_subscriptions(rows(fx.SUBSCRIPTIONS)) == [(fx.UC1, "Chan A"), (fx.UC2, 'Chan B, the "second" one'), (fx.UC3, "Chan C")]
    assert parse_playlist_items(rows(fx.LIKES)) == [("aaaaaaaaaaa", parse_time("2026-08-01T10:00:00+00:00")),
                                                     ("lllllllllll", parse_time("2026-07-01T10:00:00+00:00"))]
    assert parse_playlist_items([["notanid", "x"], ["aaaaaaaaaaa", "garbage"]]) == [("aaaaaaaaaaa", None)]
    assert parse_playlist_index(rows(fx.PLAYLISTS_INDEX)) == [("LL", "Liked videos"), ("WL", "Watch later"), ("PLabcdefghijklmnop", "My mix")]


@pytest.mark.parametrize("name,cat", [
    ("Liked videos", "likes"), ("Liked videos-videos", "likes"), ("likes", "likes"), ("Watch later", "watch_later"),
    ("Vídeos que me gustan", "likes"), ("Ver más tarde", "watch_later"), ("Vidéos “J'aime”", "likes"),
    ("Später ansehen", "watch_later"), ("My mix", "other"), ("Gym music", "other"), ("", "other")])
def test_playlist_names_in_several_languages(name, cat):
    assert classify_playlist_name(name) == cat
