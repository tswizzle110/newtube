"""Related videos: storage, the Up next list, the background job and its budget, and the recommender's use of them."""
import asyncio
import time

import pytest

from newtube.db.connection import connect, migrate
from newtube.extract.base import BotCheck, ExtractionError, VideoSummary
from newtube.net.ratelimit import DailyCapReached
from newtube.recommend.baseline import DAY, recommend
from newtube.services import related as rel
from newtube.services.related import RelatedService

NOW = 1_800_000_000
A, B, C = "UC" + "a" * 22, "UC" + "b" * 22, "UC" + "c" * 22
V1, V2, V3, V4 = "aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc", "ddddddddddd"


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    return c


def vs(vid, title="T", channel=B, ctitle="Beta", dur=100, views=5):
    return VideoSummary(vid, title, channel, ctitle, dur, views)


class Fake:
    def __init__(self, found=None, fail=None):
        self.found, self.fail, self.calls = found or {}, fail, []
        self.searches, self.search_results = [], []

    async def search(self, query, kind="video", limit=20, *, background=False, filters=None):
        self.searches.append((query, kind, background))
        return self.search_results

    async def related(self, vid, limit=25, *, background=False):
        self.calls.append((vid, background))
        if self.fail:
            raise self.fail
        return self.found.get(vid, [])


def run(c):
    return asyncio.run(c)


@pytest.fixture
def frozen(monkeypatch):
    """The service stamps real time; the job tests pass NOW, so make the two agree."""
    monkeypatch.setattr(rel.time, "time", lambda: NOW)


def interest(conn, vid, channel=A, ts=NOW - DAY):
    conn.execute("INSERT OR IGNORE INTO channels(id,title,created_at) VALUES (?,?,1)", (channel, "Alpha"))
    conn.execute("INSERT OR IGNORE INTO videos(id,channel_id,title,first_seen_at) VALUES (?,?,?,1)", (vid, channel, "liked one"))
    conn.execute("INSERT INTO playlist_items(playlist_id, video_id, added_at, position) SELECT id, ?, ?, 1 FROM playlists WHERE kind='liked'", (vid, ts))


# ---- storing and serving ----------------------------------------------------------------------------

def test_a_fetch_stores_the_list_and_the_videos_in_it(conn):
    svc = RelatedService(conn, Fake({V1: [vs(V2, "Two"), vs(V3, "Three", channel=None, ctitle=None)]}))
    cards = run(svc.for_video(V1))
    assert [c["id"] for c in cards] == [V2, V3] and cards[0]["channel_title"] == "Beta" and cards[0]["title"] == "Two"
    assert conn.execute("SELECT title FROM channels WHERE id=?", (B,)).fetchone()[0] == "Beta"
    assert conn.execute("SELECT channel_id FROM videos WHERE id=?", (V3,)).fetchone()[0] is None      # unknown channel stays unknown
    assert svc.fresh(V1)


def test_a_fresh_list_is_not_fetched_again_but_a_stale_one_is(conn):
    ex = Fake({V1: [vs(V2)]})
    svc = RelatedService(conn, ex)
    run(svc.for_video(V1)); run(svc.for_video(V1))
    assert len(ex.calls) == 1
    conn.execute("UPDATE related_fetched SET fetched_at = fetched_at - ?", (rel.TTL + 5,))
    run(svc.for_video(V1))
    assert len(ex.calls) == 2


def test_the_list_is_replaced_not_added_to(conn):
    ex = Fake({V1: [vs(V2), vs(V3)]})
    svc = RelatedService(conn, ex)
    run(svc.fetch(V1)); ex.found = {V1: [vs(V4)]}; run(svc.fetch(V1))
    assert [c["id"] for c in svc.cards(V1)] == [V4]


def test_simultaneous_requests_share_one_lookup(conn):
    ex = Fake({V1: [vs(V2)]})
    svc = RelatedService(conn, ex)

    async def both():
        await asyncio.gather(svc.for_video(V1), svc.for_video(V1), svc.for_video(V1))
    run(both())
    assert len(ex.calls) == 1


def test_failure_means_no_suggestions_not_an_error_and_is_not_retried_at_once(conn):
    ex = Fake(fail=ExtractionError("HTTP 500"))
    svc = RelatedService(conn, ex)
    assert run(svc.for_video(V1)) == [] and run(svc.for_video(V1)) == []
    assert len(ex.calls) == 1                                           # the failure is remembered for an hour
    conn.execute("UPDATE related_fetched SET fetched_at = fetched_at - ?", (rel.FAILURE_RETRY + 5,))
    run(svc.for_video(V1))
    assert len(ex.calls) == 2


def test_a_bot_check_is_reported_and_not_remembered_so_the_list_arrives_the_moment_it_clears(conn):
    ex = Fake({V1: [vs(V2)]}, fail=BotCheck("sign in"))
    svc = RelatedService(conn, ex)
    assert run(svc.lookup(V1)) == ([], "paused")
    assert conn.execute("SELECT COUNT(*) FROM related_fetched").fetchone()[0] == 0     # nothing remembered
    ex.fail = None
    cards, status = run(svc.lookup(V1))
    assert status == "ok" and [c["id"] for c in cards] == [V2]


def test_failed_and_empty_are_told_apart_and_try_again_bypasses_the_failure_memory(conn):
    ex = Fake(fail=ExtractionError("HTTP 500"))
    svc = RelatedService(conn, ex)
    assert run(svc.lookup(V1)) == ([], "failed")
    assert run(svc.lookup(V1)) == ([], "failed") and len(ex.calls) == 1        # remembered
    ex.fail = None; ex.found = {V1: [vs(V2)]}
    assert run(svc.lookup(V1, force=True))[1] == "ok"                          # Try again
    ex2 = Fake({V3: []})
    assert run(RelatedService(conn, ex2).lookup(V3)) == ([], "empty")           # a real, empty answer


def add_video(conn, vid, title, channel, published):
    conn.execute("INSERT OR IGNORE INTO channels(id,title,created_at) VALUES (?,?,1)", (channel, "Chan " + channel[2:4]))
    conn.execute("INSERT INTO videos(id,channel_id,title,published_at,first_seen_at) VALUES (?,?,?,?,1)", (vid, channel, title, published))


def test_local_suggestions_are_the_channels_other_videos_newest_first_and_never_the_video_itself(conn):
    add_video(conn, V1, "Now playing", A, 100)
    add_video(conn, V2, "Older upload", A, 50)
    add_video(conn, V3, "Newer upload", A, 200)
    add_video(conn, V4, "Other channel", B, 300)
    got = RelatedService(conn, Fake()).local_cards(V1)
    assert [c["id"] for c in got] == [V3, V2] and got[0]["channel_title"].startswith("Chan")
    assert RelatedService(conn, Fake()).local_cards("zzzzzzzzzzz") == []            # an unknown video: nothing, not an error


def test_local_suggestions_add_the_closest_titles_by_embedding(conn):
    from newtube.ml import store
    from newtube.ml.embedder import HashingEmbedder
    add_video(conn, V1, "python asyncio tutorial for beginners", A, 100)
    add_video(conn, V2, "python asyncio tutorial advanced", B, 100)
    add_video(conn, V3, "sourdough bread baking guide", B, 100)
    store.embed_pending(conn, HashingEmbedder(), limit=10)
    got = [c["id"] for c in RelatedService(conn, Fake()).local_cards(V1)]
    assert got[0] == V2 and V1 not in got                                          # the similar title ranks first


def test_endpoint_falls_back_to_local_suggestions_and_says_so(env):
    client, ctx, ex, seen = env
    add_video(ctx.conn, V1, "Now playing", A, 100)
    add_video(ctx.conn, V2, "Sibling", A, 90)
    ctx.related.extractor = Fake(fail=BotCheck("sign in"))
    body = client.get(f"/api/related/{V1}").json()
    assert body["status"] == "paused" and body["source"] == "local" and [v["id"] for v in body["videos"]] == [V2]
    ctx.related.extractor = Fake({V1: [vs(V3, "From YouTube")]})
    body = client.get(f"/api/related/{V1}?force=1").json()
    assert body["status"] == "ok" and body["source"] == "youtube" and [v["id"] for v in body["videos"]] == [V3]


def test_an_imported_video_known_only_by_id_learns_its_title_from_a_related_list(conn):
    conn.execute("INSERT INTO videos(id,title,first_seen_at,from_import) VALUES (?,'',1,1)", (V2,))
    run(RelatedService(conn, Fake({V1: [vs(V2, "Now known", dur=77)]})).fetch(V1))
    assert tuple(conn.execute("SELECT title, channel_id, duration, from_import FROM videos WHERE id=?", (V2,)).fetchone()) == ("Now known", B, 77, 1)


def test_related_videos_do_not_enter_the_subscriptions_feed(conn):
    conn.execute("INSERT INTO channels(id,title,created_at) VALUES (?,?,1)", (B, "Beta"))
    conn.execute("INSERT INTO subscriptions VALUES (?,1)", (B,))
    run(RelatedService(conn, Fake({V1: [vs(V2)]})).fetch(V1))
    from newtube.db.queries import subscriptions as subs
    assert subs.get_feed(conn) == []                                    # no upload date: not "a new upload"


# ---- the background job -----------------------------------------------------------------------------

def test_the_job_picks_your_favourites_first_and_skips_ones_it_has(conn, frozen):
    interest(conn, V1, ts=NOW - 400 * DAY)
    interest(conn, V2, ts=NOW - DAY)
    ex = Fake({V1: [vs(V3)], V2: [vs(V4)]})
    svc = RelatedService(conn, ex)
    assert run(svc.expand_step(10, now=NOW)) == "fetched" and ex.calls == [(V2, True)]       # recent first; on the background lane
    assert run(svc.expand_step(10, now=NOW)) == "fetched" and ex.calls[1] == (V1, True)
    assert run(svc.expand_step(10, now=NOW)) == "idle"                                       # everything is fetched


def test_the_job_respects_its_daily_budget_across_restarts(conn, frozen):
    for i, v in enumerate((V1, V2, V3)):
        interest(conn, v, ts=NOW - i * DAY)
    ex = Fake({})
    assert [run(RelatedService(conn, ex).expand_step(2, now=NOW)) for _ in range(3)] == ["fetched", "fetched", "capped"]
    assert run(RelatedService(conn, ex).expand_step(2, now=NOW + DAY + 5)) == "fetched"      # a day later
    assert run(RelatedService(conn, ex).expand_step(0, now=NOW)) == "idle"                   # 0 = off


def test_the_job_backs_off_on_a_bot_check_and_survives_odd_errors(conn, frozen):
    interest(conn, V1)
    assert run(RelatedService(conn, Fake(fail=BotCheck("sign in"))).expand_step(10, now=NOW)) == "capped"
    interest(conn, V2)                       # (a video that just failed is not retried for an hour, so each case needs a new one)
    assert run(RelatedService(conn, Fake(fail=DailyCapReached())).expand_step(10, now=NOW)) == "capped"
    interest(conn, V3)
    assert run(RelatedService(conn, Fake(fail=KeyError("x"))).expand_step(10, now=NOW)) == "failed"


# ---- recommendations --------------------------------------------------------------------------------

def test_videos_related_to_ones_you_like_are_recommended_even_from_unsubscribed_channels(conn):
    interest(conn, V1)
    run(RelatedService(conn, Fake({V1: [vs(V2, "A related video", channel=B, ctitle="Beta")]})).fetch(V1))
    rec = recommend(conn, now=NOW)
    assert [r.video["id"] for r in rec] == [V2] and rec[0].video["channel_title"] == "Beta"
    assert any(r.label == "Related to videos you have watched" and r.points > 0 for r in rec[0].reasons)
    assert rec[0].score == pytest.approx(sum(r.points for r in rec[0].reasons))


def test_closer_to_your_favourites_ranks_higher(conn):
    interest(conn, V1, ts=NOW - DAY)
    run(RelatedService(conn, Fake({V1: [vs(V2), vs(V3, channel="UC" + "c" * 22, ctitle="Gamma"), vs(V4, channel="UC" + "d" * 22, ctitle="Delta")]})).fetch(V1))
    assert [r.video["id"] for r in recommend(conn, now=NOW)] == [V2, V3, V4]      # list order = rank order


def test_related_videos_you_have_watched_or_hidden_are_left_out(conn):
    interest(conn, V1)
    run(RelatedService(conn, Fake({V1: [vs(V2), vs(V3), vs(V4)]})).fetch(V1))
    conn.execute("INSERT INTO events(ts,type,video_id) VALUES (?,'import_watch',?)", (NOW - 5, V2))
    conn.execute("INSERT INTO events(ts,type,video_id) VALUES (?,'not_interested',?)", (NOW - 5, V3))
    assert [r.video["id"] for r in recommend(conn, now=NOW)] == [V4]


def test_a_related_list_of_a_video_you_took_off_your_lists_stops_counting(conn):
    interest(conn, V1)
    run(RelatedService(conn, Fake({V1: [vs(V2)]})).fetch(V1))
    conn.execute("DELETE FROM playlist_items")
    assert recommend(conn, now=NOW) == []


# ---- the API ----------------------------------------------------------------------------------------

def test_related_endpoint(env):
    client, ctx, ex, seen = env
    ctx.related.extractor = Fake({"aaaaaaaaaaa": [vs(V2, "Two")]})
    r = client.get("/api/related/aaaaaaaaaaa?limit=5")
    body = r.json()
    assert r.status_code == 200 and body["status"] == "ok" and [c["id"] for c in body["videos"]] == [V2]
    assert body["videos"][0]["seen"] is False
    assert client.get("/api/related/bad").status_code == 400
    ctx.related.extractor = Fake(fail=ExtractionError("down"))
    assert client.get("/api/related/bbbbbbbbbbb").json() == {"videos": [], "status": "failed", "source": "youtube"}
    ctx.related.extractor = Fake(fail=BotCheck("sign in"))
    assert client.get("/api/related/ccccccccccc").json() == {"videos": [], "status": "paused", "source": "youtube"}


from tests.test_api import env  # noqa: E402,F401


def test_an_empty_mix_is_logged_with_what_youtube_sent(caplog):
    from newtube.extract.ytdlp import YtDlpExtractor

    async def raw(self, video_id, limit=25, *, background=False):
        return {"_type": "playlist", "title": "Mix - Seed", "entries": [{"id": video_id, "title": "Seed", "ie_key": "Youtube"}]}

    ex = object.__new__(YtDlpExtractor)
    ex.raw_related = raw.__get__(ex)
    with caplog.at_level("WARNING", logger="newtube.ytdlp"):
        assert asyncio.run(ex.related(V1)) == []            # only the seed video itself came back
    assert "no usable videos" in caplog.text and "1 entries" in caplog.text and "Mix - Seed" in caplog.text


def title_row(conn, vid, title):
    conn.execute("INSERT OR IGNORE INTO videos(id,title,first_seen_at) VALUES (?,?,1)", (vid, title))


def test_an_empty_mix_falls_back_to_searching_for_the_title(conn):
    title_row(conn, V1, "How sourdough works")
    ex = Fake({V1: []})
    ex.search_results = [vs(V1, "How sourdough works"), vs(V2, "Sourdough starter guide"), vs(V3, "Football goals of the season")]
    svc = RelatedService(conn, ex)
    cards, status = run(svc.lookup(V1))
    assert status == "ok" and [c["id"] for c in cards] == [V2]                      # (never the video itself, nor another genre)
    assert ex.searches == [("How sourdough works", "video", False)]


def test_a_video_with_no_known_title_is_not_searched(conn):
    ex = Fake({V1: []})
    assert run(RelatedService(conn, ex).lookup(V1)) == ([], "empty") and ex.searches == []


def test_after_three_empty_mixes_the_mix_is_skipped_and_titles_are_searched_directly(conn):
    ex = Fake()
    svc = RelatedService(conn, ex)
    for v in (V1, V2, V3, V4):
        title_row(conn, v, "title " + v)
        run(svc.lookup(v))
    assert len(ex.calls) == 3 and len(ex.searches) == 4          # the 4th video never asked for a Mix
    svc._mix_skip_until = 0                                       # six hours later: one Mix is tried again
    title_row(conn, "eeeeeeeeeee", "another")
    run(svc.lookup("eeeeeeeeeee"))
    assert len(ex.calls) == 4


def test_a_working_mix_resets_the_miss_count(conn):
    ex = Fake({V1: [vs(V2)]})
    svc = RelatedService(conn, ex)
    svc._mix_misses = 2
    run(svc.lookup(V1))
    assert svc._mix_misses == 0 and ex.searches == []


def test_a_mix_read_as_a_single_video_is_detected_and_not_asked_again_for_a_week(conn):
    from newtube.extract.base import RelatedUnsupported
    title_row(conn, V1, "sourdough one"); title_row(conn, V2, "sourdough two")
    ex = Fake(fail=RelatedUnsupported("one video"))
    ex.search_results = [vs(V3, "found by title: sourdough")]
    svc = RelatedService(conn, ex)
    assert [c["id"] for c in run(svc.lookup(V1))[0]] == [V3]            # this very video is still served, by title
    run(svc.lookup(V2))
    assert len(ex.calls) == 1 and len(ex.searches) == 2                 # the second video never asked for a Mix
    assert RelatedService(conn, ex)._mix_skip_until > time.time() + 6 * DAY   # ...and a restart remembers it
    conn.execute("UPDATE settings SET value='0' WHERE key=?", (rel.MIX_SKIP_KEY,))
    again = RelatedService(conn, ex)
    title_row(conn, V4, "four"); run(again.lookup(V4))
    assert len(ex.calls) == 2                                           # a week later: one more try


def test_ytdlp_reading_the_mix_address_as_a_video_is_reported_as_unsupported():
    from newtube.extract.base import RelatedUnsupported
    from newtube.extract.ytdlp import YtDlpExtractor

    async def raw(self, video_id, limit=25, *, background=False):
        return {"_type": "video", "id": video_id, "title": "Me at the zoo", "formats": [{"format_id": "18"}]}

    ex = object.__new__(YtDlpExtractor)
    ex.raw_related = raw.__get__(ex)
    with pytest.raises(RelatedUnsupported):
        asyncio.run(ex.related(V1))


# ---- only results about the same subject ---------------------------------------------------------------

def sourdough(conn, tags='["sourdough","bread","baking"]'):
    conn.execute("INSERT OR IGNORE INTO channels(id,title,created_at) VALUES (?,?,1)", (B, "Beta"))
    conn.execute("INSERT INTO videos(id,channel_id,title,tags,first_seen_at) VALUES (?,?,?,?,1)",
                 (V1, B, "Why sourdough starters fail (and how to fix it)", tags))


RESULTS = [("same topic", "Sourdough starter guide for beginners"), ("adjacent", "No-knead bread recipe, crusty loaf at home"),
           ("same words, other genre", "Why Python starters fail: how to fix your build"), ("unrelated", "Minecraft survival episode 14"),
           ("generic how-to", "How to fix it: ten tips and why they fail")]


def search_with(conn, results=RESULTS, channel=A, **kw):
    ex = Fake({V1: []})
    ex.search_results = [VideoSummary(f"r{i:010d}", title, channel, "Some Channel", 100, 5) for i, (_k, title) in enumerate(results)]
    conn.execute("DELETE FROM related_fetched"); conn.execute("DELETE FROM related_videos")       # (each call asks afresh)
    svc = RelatedService(conn, ex, type("S", (), {"related_strictness": kw.get("level", "normal"), "embedding_model": ""})())
    cards, status = run(svc.lookup(V1))
    return [c["title"] for c in cards], status, svc


def test_results_about_other_genres_are_dropped(conn):
    sourdough(conn)
    titles, status, _svc = search_with(conn)
    assert "Sourdough starter guide for beginners" in titles and "No-knead bread recipe, crusty loaf at home" in titles
    assert not any(w in " ".join(titles) for w in ("Python", "Minecraft", "How to fix it"))


def test_strictness_changes_how_many_get_through(conn):
    sourdough(conn)
    loose, _s, _v = search_with(conn, level="loose")
    strict, _s, _v = search_with(conn, level="strict")
    assert set(strict) <= set(loose) and "Sourdough starter guide for beginners" in strict
    assert "No-knead bread recipe, crusty loaf at home" not in strict               # shares only one defining word


def test_a_channel_you_know_is_judged_more_leniently(conn):
    sourdough(conn)
    conn.execute("INSERT INTO channels(id,title,created_at) VALUES (?,?,1)", (C, "Known"))
    conn.execute("INSERT INTO subscriptions VALUES (?,1)", (C,))
    ex = Fake({V1: []})
    ex.search_results = [VideoSummary("known000001", "Bread prices in 2024", C, "Known", 100, 5),
                         VideoSummary("stranger0001", "Bread prices in 2024", A, "Stranger", 100, 5)]
    svc = RelatedService(conn, ex, type("S", (), {"related_strictness": "strict", "embedding_model": ""})())
    got = [c["id"] for c in run(svc.lookup(V1))[0]]
    assert got == ["known000001"]                  # the subscribed channel's video passes one level looser; the stranger's does not


def test_the_videos_own_channel_always_counts(conn):
    sourdough(conn)
    ex = Fake({V1: []})
    ex.search_results = [VideoSummary("own00000001", "Q&A livestream replay", B, "Beta", 100, 5)]
    svc = RelatedService(conn, ex, type("S", (), {"related_strictness": "strict", "embedding_model": ""})())
    assert [c["id"] for c in run(svc.lookup(V1))[0]] == ["own00000001"]


def test_a_real_language_model_adds_a_similarity_floor(conn):
    sourdough(conn)
    ex = Fake({V1: []})
    ex.search_results = [VideoSummary("close0000001", "Sourdough starter guide", A, "X", 100, 5),
                         VideoSummary("far000000001", "Sourdough bread merchandise unboxing", A, "X", 100, 5)]

    class Model:                                   # vectors: the video and 'close' point the same way, 'far' is at right angles
        name, dim = "fake", 2
        def embed(self, texts):
            return [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]][:len(texts)]

    plain = RelatedService(conn, ex, type("S", (), {"related_strictness": "normal", "embedding_model": ""})())
    assert {c["id"] for c in run(plain.lookup(V1))[0]} == {"close0000001", "far000000001"}      # no model: words only
    conn.execute("DELETE FROM related_fetched"); conn.execute("DELETE FROM related_videos")
    svc = RelatedService(conn, ex, type("S", (), {"related_strictness": "normal", "embedding_model": "x"})())
    svc._embedder = Model()
    assert [c["id"] for c in run(svc.lookup(V1))[0]] == ["close0000001"]                         # the model drops the far one


def test_a_title_of_nothing_but_filler_cannot_be_judged_so_nothing_unknown_gets_through(conn):
    conn.execute("INSERT INTO videos(id,title,first_seen_at) VALUES (?,?,1)", (V1, "How to: top 10 tips"))
    ex = Fake({V1: []})
    ex.search_results = [vs(V2, "Anything at all")]
    assert run(RelatedService(conn, ex).lookup(V1))[0] == []
