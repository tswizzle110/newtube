"""How long a looked-up video (with its signed stream links) is reused: until just before the links really expire."""
from newtube.extract.base import Format, VideoInfo
from newtube.services import videos as vs

NOW = 1_800_000_000


def info(*urls):
    fm = [Format(str(i), u, "mp4", "avc1", None, height=360) for i, u in enumerate(urls)]
    return VideoInfo("aaaaaaaaaaa", "T", None, None, "", 10, 1, 1, None, [], None, False, 0, formats=fm, fetched_at=1)


def test_cache_lasts_until_shortly_before_the_earliest_link_expires():
    a = f"https://rr1.googlevideo.com/videoplayback?expire={NOW + 3 * 3600}&id=x"
    b = f"https://rr1.googlevideo.com/videoplayback?id=y&expire={NOW + 6 * 3600}"
    assert vs.cache_seconds(info(a, b), NOW) == 3 * 3600 - vs.EXPIRY_MARGIN


def test_long_lived_links_are_capped_and_nearly_dead_ones_get_a_floor():
    far = f"https://rr1.googlevideo.com/videoplayback?expire={NOW + 20 * 3600}"
    soon = f"https://rr1.googlevideo.com/videoplayback?expire={NOW + 60}"
    assert vs.cache_seconds(info(far), NOW) == vs.MAX_TTL
    assert vs.cache_seconds(info(soon), NOW) == vs.MIN_TTL


def test_no_expiry_in_the_links_keeps_the_fixed_time():
    assert vs.cache_seconds(info("https://rr1.googlevideo.com/videoplayback?id=x"), NOW) == vs.INFO_TTL
    assert vs.cache_seconds(info(), NOW) == vs.INFO_TTL
