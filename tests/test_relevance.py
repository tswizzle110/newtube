"""The same-subject filter for Up next: what counts as a defining word, and when a result is kept."""
from newtube.services import relevance as rv

CORPUS = rv.Corpus(["Sourdough bread for beginners", "Python tutorial: build a web app", "Minecraft survival episode 3",
                    "How to fix a leaking tap", "Why your build fails", "Best fixes for slow computers", "Sourdough starter tips"])


def topic(title, tags=None, category=None):
    import json
    return rv.topic_of(title, json.dumps(tags) if tags else None, category, CORPUS)


def test_filler_numbers_and_short_words_are_not_subject_words():
    assert rv.tokens("Why 10 of the BEST sourdough tips are wrong!") == ["sourdough"]
    assert rv.tokens("Starters and starter") == ["starter", "starter"]            # plural and singular are one word
    assert rv.tokens(None) == [] and rv.tokens("") == []


def test_rare_words_and_tags_define_a_video():
    t = topic("Why sourdough starters fail (and how to fix it)", ["bread", "baking"])
    assert {"sourdough", "bread", rv.tokens("baking")[0]} <= set(t.defining)
    assert t.defining[0] in (rv.tokens("baking")[0], "bread")                         # a tag outranks the title's own words
    assert "fix" not in t.defining and "why" not in t.defining


def test_same_subject_passes_and_another_genre_does_not():
    t = topic("Why sourdough starters fail (and how to fix it)", ["sourdough", "bread"])
    assert rv.judge(t, "Sourdough starter guide for beginners") is not None
    assert rv.judge(t, "No-knead bread recipe, crusty loaf at home") is not None        # one strong (tag) word is enough
    assert rv.judge(t, "Why Python starters fail: how to fix your build") is None        # one ordinary word is not
    assert rv.judge(t, "Minecraft survival episode 14") is None


def test_levels_are_nested_and_stricter_means_fewer():
    t = topic("Sourdough bread starter feeding schedule", ["sourdough", "bread", "baking"])
    candidates = ["Baking bread at home", "Sourdough bread crust secrets", "Feeding the ducks at the park", "Tax tips"]
    got = {lvl: {c for c in candidates if rv.judge(t, c, lvl) is not None} for lvl in rv.LEVELS}
    assert got["strict"] <= got["normal"] <= got["loose"]
    assert "Tax tips" not in got["loose"] and "Feeding the ducks at the park" in got["loose"]
    assert "Feeding the ducks at the park" not in got["normal"] and "Baking bread at home" in got["strict"]


def test_trusted_channels_are_judged_one_level_lighter():
    assert [rv.relax(x) for x in rv.LEVELS] == ["loose", "loose", "normal"]


def test_a_title_with_no_subject_words_cannot_be_judged():
    t = topic("How to: top 10 tips")
    assert t.defining == [] and rv.judge(t, "Anything") is None


def test_coverage_is_higher_when_more_defining_words_are_shared():
    t = topic("Sourdough bread starter feeding", ["sourdough", "bread", "starter"])
    assert rv.judge(t, "Sourdough bread starter feeding guide", "loose") > rv.judge(t, "Sourdough tips", "loose") > 0


def test_a_subject_you_watch_a_lot_does_not_count_for_less():
    many = rv.Corpus([f"Sourdough bread recipe number {i}" for i in range(400)] + ["Something else entirely"] * 100)
    assert many.weight("sourdough") < rv.Corpus([]).weight("sourdough")              # (in 80% of titles: now treated as filler) ...
    mixed = rv.Corpus([f"Sourdough loaf {i}" for i in range(40)] + [f"Topic {i} words here" for i in range(360)])
    assert mixed.weight("sourdough") == rv.Corpus([]).weight("sourdough")            # ... but 10% is a hobby, not filler


def test_a_longer_word_is_a_little_more_specific_and_a_tag_counts_more():
    assert CORPUS.weight("sourdough") > CORPUS.weight("tap")
    t = topic("Sourdough starter", ["bread"])
    assert t.defining[0] in ("bread", "sourdough") and t.weight["bread"] > rv.Corpus([]).weight("bread")
