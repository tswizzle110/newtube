"""Watch-time statistics: real playback only, your own days, and a start date you control."""
import datetime as dt
import json
from zoneinfo import ZoneInfo

import pytest

from newtube.db.connection import connect, migrate
from newtube.db.queries import history, stats
from newtube.db.queries.events import log_event

UTC, NY = ZoneInfo("UTC"), ZoneInfo("America/New_York")
CH = "UC" + "a" * 22


def ts(y, m, d, hh=12, mm=0, tz=UTC):
    return int(dt.datetime(y, m, d, hh, mm, tzinfo=tz).timestamp())


NOW = ts(2026, 11, 3, 15)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    c.execute("INSERT INTO channels(id,title,created_at) VALUES (?,?,1)", (CH, "Alpha"))
    return c


def session(conn, sid, video, started, intervals, duration=1200.0, title=None, channel=CH):
    conn.execute("INSERT OR IGNORE INTO videos(id,channel_id,title,first_seen_at) VALUES (?,?,?,1)", (video, channel, title or video))
    conn.execute("INSERT INTO watch_sessions(id,video_id,started_at,intervals,max_position,duration) VALUES (?,?,?,?,?,?)",
                 (sid, video, started, json.dumps(intervals), max((b for _, b in intervals), default=0), duration))


V1, V2, V3 = "aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"


def test_an_empty_database_gives_zeros_not_an_error(conn):
    s = stats.watch_stats(conn, UTC, NOW)
    assert (s["total_seconds"], s["sessions"], s["videos"], s["longest_day"]) == (0, 0, 0, None)
    assert [d["date"] for d in s["days"]] == ["2026-11-03"] and s["since"] == "2026-11-03"


def test_total_and_days_add_up_and_empty_days_are_listed(conn):
    session(conn, "s1", V1, ts(2026, 11, 1), [[0, 600]])                       # 10 min on the 1st
    session(conn, "s2", V2, ts(2026, 11, 1, 20), [[0, 300]])                   # 5 min the same day
    session(conn, "s3", V3, ts(2026, 11, 3, 9), [[0, 120]])                    # 2 min today; nothing on the 2nd
    s = stats.watch_stats(conn, UTC, NOW)
    assert s["since"] == "2026-11-01" and s["since_is_automatic"] is True
    assert [(d["date"], d["seconds"]) for d in s["days"]] == [("2026-11-01", 900), ("2026-11-02", 0), ("2026-11-03", 120)]
    assert s["total_seconds"] == 1020 == sum(d["seconds"] for d in s["days"])
    assert (s["sessions"], s["videos"], s["active_days"], s["days_counted"], s["average_per_day"]) == (3, 3, 2, 3, 340)
    assert s["longest_day"] == {"date": "2026-11-01", "seconds": 900}


def test_replaying_the_same_part_in_one_session_is_not_counted_twice_and_never_exceeds_the_video(conn):
    session(conn, "s1", V1, ts(2026, 11, 3, 9), [[0, 100], [50, 150], [140, 200]])         # one stretch 0-200 = 200 s
    session(conn, "s2", V2, ts(2026, 11, 3, 10), [[0, 5000]], duration=300.0)             # a bogus interval longer than the video
    assert stats.watch_stats(conn, UTC, NOW)["total_seconds"] == 200 + 300


def test_two_separate_sessions_of_the_same_video_both_count(conn):
    session(conn, "s1", V1, ts(2026, 11, 3, 9), [[0, 600]])
    session(conn, "s2", V1, ts(2026, 11, 3, 10), [[0, 600]])                   # watched the same 10 minutes again
    s = stats.watch_stats(conn, UTC, NOW)
    assert (s["total_seconds"], s["videos"], s["sessions"]) == (1200, 1, 2)


def test_days_are_your_calendar_days_not_utc_days(conn):
    session(conn, "late", V1, ts(2026, 11, 2, 23, 30, NY), [[0, 600]])         # 11:30 pm on the 2nd in New York = the 3rd in UTC
    ny = stats.watch_stats(conn, NY, ts(2026, 11, 3, 10, tz=NY))
    assert [(d["date"], d["seconds"]) for d in ny["days"]] == [("2026-11-02", 600), ("2026-11-03", 0)]
    utc = stats.watch_stats(conn, UTC, NOW)
    assert [(d["date"], d["seconds"]) for d in utc["days"]] == [("2026-11-03", 600)]


def test_a_chosen_start_date_excludes_everything_before_it(conn):
    session(conn, "old", V1, ts(2026, 10, 30), [[0, 1000]])                    # before you started
    session(conn, "new", V2, ts(2026, 11, 2, 8), [[0, 300]])
    assert stats.watch_stats(conn, UTC, NOW)["total_seconds"] == 1300 and stats.watch_stats(conn, UTC, NOW)["since"] == "2026-10-30"
    stats.save_since(conn, dt.date(2026, 11, 2))
    s = stats.watch_stats(conn, UTC, NOW)
    assert (s["since"], s["since_is_automatic"], s["total_seconds"], s["days_counted"]) == ("2026-11-02", False, 300, 2)
    assert s["average_per_day"] == 150
    stats.save_since(conn, None)                                               # back to automatic
    assert stats.watch_stats(conn, UTC, NOW)["since_is_automatic"] is True


def test_the_start_day_is_included_from_its_first_second(conn):
    session(conn, "edge", V1, ts(2026, 11, 2, 0, 0), [[0, 60]])
    stats.save_since(conn, dt.date(2026, 11, 2))
    assert stats.watch_stats(conn, UTC, NOW)["total_seconds"] == 60


def test_imported_history_never_counts(conn):
    conn.execute("INSERT INTO videos(id,title,first_seen_at,from_import) VALUES (?,'x',1,1)", (V1,))
    conn.execute("INSERT INTO events(ts,type,video_id) VALUES (?,'import_watch',?)", (ts(2026, 11, 2), V1))
    assert stats.watch_stats(conn, UTC, NOW)["total_seconds"] == 0


def test_history_removal_removes_the_time_too_but_later_watching_counts_again(conn):
    session(conn, "s1", V1, ts(2026, 11, 2, 9), [[0, 600]])
    session(conn, "s2", V2, ts(2026, 11, 2, 10), [[0, 100]])
    history.remove(conn, V1, now=ts(2026, 11, 2, 12))
    assert stats.watch_stats(conn, UTC, NOW)["total_seconds"] == 100
    session(conn, "s3", V1, ts(2026, 11, 3, 9), [[0, 60]])                     # watched again afterwards
    assert stats.watch_stats(conn, UTC, NOW)["total_seconds"] == 160


def test_top_videos_and_channels(conn):
    other = "UC" + "b" * 22
    conn.execute("INSERT INTO channels(id,title,created_at) VALUES (?,?,1)", (other, "Beta"))
    session(conn, "s1", V1, ts(2026, 11, 3, 9), [[0, 900]], title="Long one")
    session(conn, "s2", V2, ts(2026, 11, 3, 10), [[0, 100]], title="Short one", channel=other)
    session(conn, "s3", V3, ts(2026, 11, 3, 11), [[0, 200]], title="Medium", channel=other)
    s = stats.watch_stats(conn, UTC, NOW)
    assert [(v["title"], v["seconds"]) for v in s["top_videos"]] == [("Long one", 900), ("Medium", 200), ("Short one", 100)]
    assert [(c["title"], c["seconds"]) for c in s["top_channels"]] == [("Alpha", 900), ("Beta", 300)]


def test_zero_length_sessions_and_damaged_rows_are_ignored(conn):
    session(conn, "s1", V1, ts(2026, 11, 3, 9), [])
    conn.execute("INSERT INTO videos(id,title,first_seen_at) VALUES (?,'x',1)", (V2,))
    conn.execute("INSERT INTO watch_sessions(id,video_id,started_at,intervals,max_position) VALUES ('bad',?,?,'not json',0)", (V2, ts(2026, 11, 3, 9)))
    s = stats.watch_stats(conn, UTC, NOW)
    assert (s["total_seconds"], s["sessions"]) == (0, 0)


def test_the_per_day_list_is_capped_but_the_total_covers_everything(conn):
    session(conn, "ancient", V1, ts(2023, 1, 5), [[0, 600]])
    s = stats.watch_stats(conn, UTC, NOW)
    assert len(s["days"]) == stats.MAX_DAYS and s["total_seconds"] == 600 and s["since"] == "2023-01-05"


# ---- the API ----------------------------------------------------------------------------------------

def skip(conn, when, kind="sb_skip", uuid="u1", start=100, end=160, category="sponsor", video=V1):
    log_event(conn, kind, video_id=video, ts=when,
              payload={"uuid": uuid, "category": category, "start": start, "end": end})


def test_skipped_time_adds_automatic_and_button_skips_by_day_and_category(conn):
    session(conn, "s", V1, ts(2026, 11, 2), [[0, 60]])
    skip(conn, ts(2026, 11, 2), uuid="a", start=100, end=160)                                # 60 s sponsor
    skip(conn, ts(2026, 11, 2, 13), "sb_manual_skip", uuid="b", start=0, end=30, category="intro")
    skip(conn, ts(2026, 11, 3, 9), uuid="c", start=500, end=620, video=V2)                   # 120 s, next day
    r = stats.watch_stats(conn, UTC, NOW)
    assert r["skipped"]["seconds"] == 210 and r["skipped"]["count"] == 3
    assert r["skipped"]["categories"] == [{"category": "sponsor", "seconds": 180}, {"category": "intro", "seconds": 30}]
    by_day = {d["date"]: d["skipped"] for d in r["days"]}
    assert by_day["2026-11-02"] == 90 and by_day["2026-11-03"] == 120
    assert r["total_seconds"] == 60                                                          # (skipped time is not watch time)


def test_an_undone_skip_does_not_count_and_only_cancels_its_own_segment(conn):
    session(conn, "s", V1, ts(2026, 11, 2), [[0, 60]])
    skip(conn, ts(2026, 11, 2, 12, 0), uuid="a", start=100, end=160)
    skip(conn, ts(2026, 11, 2, 12, 1), uuid="b", start=300, end=340)
    skip(conn, ts(2026, 11, 2, 12, 2), "sb_undo", uuid="a", start=100, end=160)
    r = stats.watch_stats(conn, UTC, NOW)
    assert r["skipped"]["seconds"] == 40 and r["skipped"]["count"] == 1
    skip(conn, ts(2026, 11, 2, 12, 3), "sb_undo", uuid="never-skipped")                      # an undo with nothing to cancel
    assert stats.watch_stats(conn, UTC, NOW)["skipped"]["seconds"] == 40


def test_skipping_the_same_sponsor_again_after_rewinding_counts_again(conn):
    session(conn, "s", V1, ts(2026, 11, 2), [[0, 60]])
    skip(conn, ts(2026, 11, 2, 12, 0), uuid="a", start=100, end=160)
    skip(conn, ts(2026, 11, 2, 12, 5), uuid="a", start=100, end=160)
    assert stats.watch_stats(conn, UTC, NOW)["skipped"]["seconds"] == 120


def test_highlight_jumps_damaged_payloads_and_absurd_segments_are_ignored(conn):
    session(conn, "s", V1, ts(2026, 11, 2), [[0, 60]])
    skip(conn, ts(2026, 11, 2), "sb_manual_skip", category="poi_highlight", start=50, end=300)
    log_event(conn, "sb_skip", video_id=V1, ts=ts(2026, 11, 2), payload={"category": "sponsor"})      # no start/end
    conn.execute("INSERT INTO events(ts,type,video_id,payload) VALUES (?,?,?,?)", (ts(2026, 11, 2), "sb_skip", V1, "not json"))
    skip(conn, ts(2026, 11, 2), uuid="z", start=0, end=99999)                                        # capped at an hour
    skip(conn, ts(2026, 11, 2), uuid="y", start=50, end=50)                                          # zero length
    r = stats.watch_stats(conn, UTC, NOW)
    assert r["skipped"]["seconds"] == 3600 and r["skipped"]["count"] == 1


def test_skips_before_the_chosen_start_date_do_not_count(conn):
    session(conn, "s", V1, ts(2026, 11, 2), [[0, 60]])
    skip(conn, ts(2026, 10, 30), uuid="old", start=0, end=500)
    skip(conn, ts(2026, 11, 2), uuid="new", start=0, end=50)
    stats.save_since(conn, dt.date(2026, 11, 1))
    assert stats.watch_stats(conn, UTC, NOW)["skipped"]["seconds"] == 50


def test_no_skips_gives_zeros(conn):
    r = stats.watch_stats(conn, UTC, NOW)
    assert r["skipped"] == {"seconds": 0, "count": 0, "categories": []}


def test_stats_api(env):
    client, ctx, ex, seen = env
    now = int(dt.datetime.now(dt.timezone.utc).timestamp())
    ctx.conn.execute("INSERT INTO videos(id,title,first_seen_at) VALUES (?,'T',1)", (V1,))
    ctx.conn.execute("INSERT INTO watch_sessions(id,video_id,started_at,intervals,max_position,duration) VALUES ('s',?,?,?,300,1000)",
                     (V1, now - 60, json.dumps([[0, 300]])))
    r = client.get("/api/stats/watch?tz=America/New_York").json()
    assert r["total_seconds"] == 300 and r["tz"] == "America/New_York" and r["videos"] == 1
    assert client.get("/api/stats/watch?tz=Mars/Olympus").json()["tz"] == "UTC"                    # unknown zones fall back
    assert client.get("/api/stats/watch").json()["tz"] == "UTC"
    today = dt.date.today().isoformat()
    assert client.put("/api/stats/since", json={"since": today}).json() == {"since": today}
    assert client.get("/api/stats/watch").json()["since_is_automatic"] is False
    assert client.put("/api/stats/since", json={"since": None}).json() == {"since": None}
    for bad in ("yesterday", "2026-13-40", "1999-01-01", "2999-01-01"):
        assert client.put("/api/stats/since", json={"since": bad}).status_code == 400


from tests.test_api import env  # noqa: E402,F401
