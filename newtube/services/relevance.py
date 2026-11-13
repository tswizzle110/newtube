"""Is a search result really about the same thing as the video you are watching?

Up next, when YouTube's own list is not available, searches for the video's title. A search returns anything that shares
words with the title, including videos from a different world ("Why Python starters fail" for a sourdough video). This
module keeps only the results about the same subject, using what this server already knows and nothing it has to ask for.

How it judges, in plain terms:

* **Defining words.** The words that say what the video is about: its title and tags, minus filler ("how", "why",
  "best", "tutorial", numbers ...). Tags count extra: creators put the genre there. (Words are NOT ranked by rarity: the
  more you watch a subject, the more common its words become in your own titles, and that must not count against them.)
* **Sharing them.** A result must share enough of those defining words. How much depends on the strictness setting:
  loose: any; normal: one strong word (one of the top two) or two ordinary ones; strict: at least two (see ``judge``).
* **Trust.** A result from a channel you subscribe to or watch is judged one step more leniently than one from a channel
  you have never seen; a result from the video's own channel always counts.
* **A real language model, when you have one**, adds a second check on top: its similarity to the video must clear a floor.
  (The built-in word-hashing embedder cannot tell genres apart: it sees "starters" as "starters", so it is not used for this.)
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass, field

LEVELS = ("loose", "normal", "strict")

# Words that carry no subject: function words, and the filler that fills YouTube titles.
_STOP = set("""
a an and are as at be but by for from has have he her his how i if in into is it its just me my no not of on or our she so
than that the their them then there these they this to too up us was we were what when where which who why will with you your
about after again all also any been before being both can could did do does done down each even every get got had here
more most much must now off once only other out over own same should some such than through under until very would
""".split())
_FILLER = set("""
best top new full official video videos part episode ep vs versus review tips tricks guide tutorial beginners beginner
explained explain ultimate complete free make made making using use used need know things thing way ways reason reasons
real truth really actually simple easy fast quick perfect perfectly better worst first last one two three four five six seven
eight nine ten live stream shorts short clip clips compilation highlights trailer interview official lyrics audio
fix fixed fixing fail fails failed failing wrong right work works working
""".split())
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
TAG_BONUS = 0.75            # tags are the creator naming the genre: weigh them above title words
DEFINING = 6                # how many words describe a video


def stem(word: str) -> str:
    """Just enough to treat 'starters' and 'starter' as one word. (Nothing fancy: this must stay predictable.)"""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    if len(word) > 5 and word.endswith("ing"):
        return word[:-3]
    return word


def tokens(text: str | None) -> list[str]:
    """The subject words of a text: lower case, no numbers, no filler, one form per word."""
    out = []
    for w in _WORD.findall((text or "").lower()):
        if len(w) < 3 or w.isdigit() or w in _STOP or w in _FILLER:
            continue
        out.append(stem(w))
    return out


class Corpus:
    """How common each word is among the titles this server knows. Used ONLY to mark down words that are in a large share
    of all titles (filler this module's lists did not think of). It deliberately does not reward rare words: the more you
    watch a subject, the more common its words get in your own titles, and that must not make them count for less."""

    COMMON_SHARE = 0.15          # a word in more than this share of titles is filler...
    MIN_TITLES = 200             # ...but only once there are enough titles to say so

    def __init__(self, titles) -> None:
        self.n = 0
        self.df: Counter[str] = Counter()
        for t in titles:
            self.n += 1
            self.df.update(set(tokens(t)))

    def weight(self, word: str) -> float:
        w = 1.0 + 0.05 * min(len(word), 12)                # a longer word is a little more specific
        if self.n >= self.MIN_TITLES and self.df.get(word, 0) / self.n > self.COMMON_SHARE:
            w *= 0.3
        return w


_cache: dict[int, tuple[float, tuple, Corpus]] = {}


def corpus_for(conn: sqlite3.Connection, ttl: float = 600.0) -> Corpus:
    """The corpus of every known title, rebuilt when the set of titles changes or after ``ttl`` seconds."""
    # (a fingerprint of the content, not just the count: an object id can be reused by a different database)
    stamp = tuple(conn.execute("SELECT count(*), COALESCE(max(rowid), 0), COALESCE(sum(length(title)), 0)"
                               " FROM videos WHERE title <> ''").fetchone())
    hit = _cache.get(id(conn))
    if hit and hit[1] == stamp and time.monotonic() - hit[0] < ttl:
        return hit[2]
    corpus = Corpus(r[0] for r in conn.execute("SELECT title FROM videos WHERE title <> ''"))
    _cache[id(conn)] = (time.monotonic(), stamp, corpus)
    return corpus


@dataclass
class Topic:
    """What a video is about: its defining words, strongest first, and how much each one counts."""
    defining: list[str] = field(default_factory=list)
    weight: dict[str, float] = field(default_factory=dict)


def topic_of(title: str | None, tags_json: str | None, category: str | None, corpus: Corpus) -> Topic:
    pool: dict[str, float] = {}
    for w in tokens(title):
        pool[w] = corpus.weight(w)
    try:
        tags = [str(t) for t in json.loads(tags_json)[:15]] if tags_json else []
    except (ValueError, TypeError):
        tags = []
    for w in tokens(" ".join(tags)) + tokens(category):
        pool[w] = max(pool.get(w, 0.0), corpus.weight(w)) + TAG_BONUS
    ranked = sorted(pool, key=lambda w: (-pool[w], w))[:DEFINING]
    return Topic(ranked, {w: pool[w] for w in ranked})


def relax(level: str) -> str:
    """One step more lenient (for channels you already know)."""
    return {"strict": "normal", "normal": "loose"}.get(level, "loose")


def judge(topic: Topic, candidate_title: str, level: str = "normal") -> float | None:
    """How well the candidate matches the video's subject, or None when it is not about the same thing.

    The score is the weight of the defining words it shares, measured in multiples of the video's strongest word
    (so 1.0 means "about as much as its single best word"). Needed, by level:
      loose   any defining word at all
      normal  1.0, or 0.75 when one of the video's top two words is among them (one strong word is enough, a weak one is not)
      strict  1.5, or 1.0 with at least two of its words"""
    if not topic.defining:
        return None                                   # nothing to go on (a title of nothing but filler)
    words = set(tokens(candidate_title))
    shared = [w for w in topic.defining if w in words]
    if not shared:
        return None
    score = sum(topic.weight[w] for w in shared) / max(topic.weight.values())
    strong = any(w in words for w in topic.defining[:2])
    if level == "strict":
        ok = score >= 1.5 or (len(shared) >= 2 and score >= 1.0)
    elif level == "normal":
        ok = score >= 1.0 or (strong and score >= 0.75)
    else:
        ok = True
    return score if ok else None
