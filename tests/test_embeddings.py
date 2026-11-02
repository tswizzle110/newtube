"""Embeddings: the store and queue, the taste vector, and the 'similar to what you watch' term in the ranker."""
import asyncio

import pytest

from newtube.config import Settings
from newtube.db.connection import connect, migrate
from newtube.jobs.scheduler import Scheduler
from newtube.ml import store, taste
from newtube.ml.embedder import HashingEmbedder, ModelNotInstalled, SentenceTransformerEmbedder, load_embedder
from newtube.recommend.baseline import DAY, recommend

NOW = 1_800_000_000
A, B = "UC" + "a" * 22, "UC" + "b" * 22
EMB = HashingEmbedder()


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    for cid, name in ((A, "Alpha"), (B, "Beta")):
        c.execute("INSERT INTO channels(id,title,created_at) VALUES (?,?,1)", (cid, name))
    return c


def video(conn, vid, title, channel=A, age_days=1.0, tags=None, desc=None, from_import=0):
    conn.execute("INSERT INTO videos(id,channel_id,title,published_at,first_seen_at,tags,description,from_import)"
                 " VALUES (?,?,?,?,1,?,?,?)", (vid, channel, title, int(NOW - age_days * DAY), tags, desc, from_import))


def cos(a, b):
    return store.dot(a, b)


# ---- the embedder -----------------------------------------------------------------------------------

def test_vectors_are_unit_length_and_similar_text_is_closer():
    cat1, cat2, car = EMB.embed(["funny cats playing piano", "cats playing the piano", "how to change a car tyre"])
    assert abs(cos(cat1, cat1) - 1) < 1e-9
    assert cos(cat1, cat2) > 0.5 > cos(cat1, car) + 0.3


def test_the_embedding_is_deterministic_across_runs():
    assert HashingEmbedder().embed(["same text"]) == HashingEmbedder().embed(["same text"])


def test_empty_text_does_not_crash():
    (v,) = EMB.embed([""])
    assert len(v) == EMB.dim


def test_vectors_survive_storage_exactly_enough():
    (v,) = EMB.embed(["round trip"])
    back = store.unpack(store.pack(v))
    assert len(back) == len(v) and max(abs(x - y) for x, y in zip(v, back)) < 1e-6


def test_a_missing_model_folder_says_how_to_fix_it(tmp_path):
    with pytest.raises(ModelNotInstalled, match="Download the model files yourself"):
        SentenceTransformerEmbedder(tmp_path / "nope")
    with pytest.raises(ModelNotInstalled):
        load_embedder(Settings(data_dir=tmp_path, embedding_model="nope"))
    assert isinstance(load_embedder(Settings(data_dir=tmp_path)), HashingEmbedder)


def test_the_text_a_model_reads(conn):
    assert store.video_text("Title", "Chan", "Music", '["a","b"]', "x" * 1000) == "Title. Chan. Music. a b. " + "x" * 400
    assert store.video_text("Only title", None, None, None, None) == "Only title"
    assert store.video_text("T", "C", None, "not json", None) == "T. C"


# ---- the queue --------------------------------------------------------------------------------------

def test_pending_videos_are_embedded_once_and_blank_titles_are_skipped(conn):
    video(conn, "aaaaaaaaaaa", "Cats"); video(conn, "bbbbbbbbbbb", "")
    assert store.count_pending(conn, EMB.name) == 1
    assert store.embed_pending(conn, EMB, now=NOW) == 1 and store.embed_pending(conn, EMB, now=NOW) == 0
    assert list(store.load_vectors(conn, EMB.name)) == ["aaaaaaaaaaa"]


def test_a_video_is_embedded_again_when_richer_details_arrive(conn):
    video(conn, "aaaaaaaaaaa", "Cats")
    store.embed_pending(conn, EMB, now=1000)
    old = store.load_vectors(conn, EMB.name)["aaaaaaaaaaa"]
    conn.execute("UPDATE videos SET detail_fetched_at=2000, description='piano piano' WHERE id='aaaaaaaaaaa'")
    assert store.embed_pending(conn, EMB, now=3000) == 1
    assert store.load_vectors(conn, EMB.name)["aaaaaaaaaaa"] != old and store.embed_pending(conn, EMB, now=4000) == 0


def test_a_different_model_re_embeds_everything(conn):
    video(conn, "aaaaaaaaaaa", "Cats")
    store.embed_pending(conn, EMB, now=1)
    other = HashingEmbedder(64)
    assert store.count_pending(conn, other.name) == 1 and store.embed_pending(conn, other, now=2) == 1
    assert store.load_vectors(conn, EMB.name) == {} and len(store.load_vectors(conn, other.name)["aaaaaaaaaaa"]) == 64


def test_videos_people_interact_with_are_embedded_first(conn):
    for i in range(5):
        video(conn, f"vid{i:08d}", f"title {i}")
    conn.execute("INSERT INTO events(ts,type,video_id) VALUES (1,'import_watch','vid00000003')")
    assert [r["id"] for r in store.pending(conn, EMB.name, 2)][0] == "vid00000003"


def test_the_background_embedder_works_off_the_main_thread(conn):
    video(conn, "aaaaaaaaaaa", "Cats")
    assert asyncio.run(store.embed_pending_async(conn, EMB)) == 1 and store.count_pending(conn, EMB.name) == 0


def test_the_scheduler_embeds_when_idle_and_survives_a_missing_model(conn, tmp_path):
    video(conn, "aaaaaaaaaaa", "Cats")
    s = Scheduler(Settings(data_dir=tmp_path), conn, http=None, extractor=None)
    assert asyncio.run(s.embed_step()) == "embedded" and asyncio.run(s.embed_step()) == "idle"
    broken = Scheduler(Settings(data_dir=tmp_path, embedding_model="nope"), conn, http=None, extractor=None)
    assert asyncio.run(broken.embed_step()) == "idle" and broken._embed_off              # logged once, then left alone


# ---- taste and ranking ------------------------------------------------------------------------------

def watched(conn, vid, title, channel=B):
    video(conn, vid, title, channel=channel, age_days=400, from_import=1)
    conn.execute("INSERT INTO events(ts,type,video_id) VALUES (?,?,?)", (NOW - DAY, "import_watch", vid))


def test_taste_is_none_without_history_or_embeddings(conn):
    video(conn, "aaaaaaaaaaa", "Cats"); store.embed_pending(conn, EMB)
    assert taste.taste_vector(conn, EMB.name, NOW) is None and taste.scorer(conn, EMB.name, NOW) is None


def test_taste_points_towards_what_you_watch(conn):
    for i in range(3):
        watched(conn, f"cat{i:08d}", f"funny cats playing piano {i}")
    video(conn, "newcat00000", "cats playing piano again"); video(conn, "newcar00000", "how to change a car tyre")
    store.embed_pending(conn, EMB, 100)
    sims = taste.scorer(conn, EMB.name, NOW)(["newcat00000", "newcar00000"])
    assert sims["newcat00000"] > sims["newcar00000"] + 0.2


def test_the_ranker_prefers_videos_similar_to_your_history_and_says_so(conn):
    conn.execute("INSERT INTO subscriptions VALUES (?,1)", (A,))
    for i in range(3):
        watched(conn, f"cat{i:08d}", f"funny cats playing piano {i}")
    video(conn, "newcar00000", "how to change a car tyre", age_days=0.5)                   # fresher
    video(conn, "newcat00000", "cats playing piano again", age_days=2)
    store.embed_pending(conn, EMB, 100)
    plain = [r.video["id"] for r in recommend(conn, now=NOW)]
    assert plain == ["newcar00000", "newcat00000"]                                           # freshness wins without taste
    ranked = recommend(conn, now=NOW, taste=taste.scorer(conn, EMB.name, NOW))
    assert [r.video["id"] for r in ranked][0] == "newcat00000"
    why = {r.label: r.points for r in ranked[0].reasons}
    assert why["Similar to videos you have watched"] > 0
    assert ranked[0].score == pytest.approx(sum(r.points for r in ranked[0].reasons))


def test_the_taste_vector_is_reused_until_your_history_changes(conn, monkeypatch):
    watched(conn, "cat00000001", "funny cats"); store.embed_pending(conn, EMB)
    calls = []
    real = taste.taste_vector
    monkeypatch.setattr(taste, "taste_vector", lambda *a, **k: calls.append(1) or real(*a, **k))
    taste.scorer(conn, EMB.name, NOW); taste.scorer(conn, EMB.name, NOW)
    assert len(calls) == 1
    conn.execute("INSERT INTO events(ts,type,video_id) VALUES (?,?,?)", (NOW, "import_like", "cat00000001"))
    taste.scorer(conn, EMB.name, NOW)
    assert len(calls) == 2


def test_recommendations_still_work_when_the_model_is_missing(conn, tmp_path):
    from newtube.api import recommendations as api
    assert api.router is not None                                        # (the endpoint swallows a missing model: see below)
    ctx = type("C", (), {"conn": conn, "settings": Settings(data_dir=tmp_path, embedding_model="nope")})()
    conn.execute("INSERT INTO subscriptions VALUES (?,1)", (A,)); video(conn, "aaaaaaaaaaa", "Cats", age_days=0.1)
    out = asyncio.run(api.recommendations(10, ctx))
    assert [c["id"] for c in out] == ["aaaaaaaaaaa"]
