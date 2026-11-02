"""SponsorBlock data types and the user-facing settings for them."""

from __future__ import annotations

from dataclasses import dataclass, field

# Categories we ask for. (SponsorBlock has a few more, e.g. "hook" and "chapter"; add here when wanted.)
CATEGORIES = ("sponsor", "selfpromo", "interaction", "intro", "outro", "preview", "filler",
              "music_offtopic", "exclusive_access", "poi_highlight")
LABELS = {
    "sponsor": "Sponsor", "selfpromo": "Unpaid / self promotion", "interaction": "Interaction reminder",
    "intro": "Intermission / intro", "outro": "Endcards / credits", "preview": "Preview / recap",
    "filler": "Filler tangent", "music_offtopic": "Music: non-music section",
    "exclusive_access": "Exclusive access", "poi_highlight": "Highlight",
}
# What to do with a category: skip on its own, offer a skip button, only mark the seek bar, or ignore.
ACTIONS = ("auto", "button", "mark", "ignore")
DEFAULT_ACTIONS = {
    "sponsor": "auto", "selfpromo": "auto", "interaction": "auto",
    "intro": "button", "outro": "button", "preview": "button", "filler": "button",
    "music_offtopic": "mark", "exclusive_access": "mark", "poi_highlight": "button",
}


@dataclass
class Segment:
    uuid: str
    start: float
    end: float                  # == start for a point of interest (highlight)
    category: str
    action: str                 # SponsorBlock's actionType: 'skip' | 'mute' | 'poi'
    votes: int = 0
    locked: bool = False
    video_duration: float = 0.0  # length of the video when this was submitted (0 = unknown)
    description: str = ""


@dataclass
class SegmentSet:
    segments: list[Segment] = field(default_factory=list)
    # Whole-video labels ("this video is a sponsored ad"): shown as a badge, never skipped.
    full_labels: list[str] = field(default_factory=list)
