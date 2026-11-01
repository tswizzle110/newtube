import pytest

from newtube.media.diskcache import DiskCache
from newtube.media.ranges import CHUNK, clamp_range


@pytest.mark.parametrize("hdr,expected", [
    (None, f"bytes=0-{CHUNK - 1}"),
    ("bytes=0-", f"bytes=0-{CHUNK - 1}"),
    ("bytes=100-199", "bytes=100-199"),
    ("bytes=100-", f"bytes=100-{100 + CHUNK - 1}"),
    (f"bytes=5-{10 * CHUNK}", f"bytes=5-{5 + CHUNK - 1}"),
    ("bytes=-500", "bytes=-500"),
    ("bytes=0-1,5-9", f"bytes=0-{CHUNK - 1}"),     # multi-range unsupported -> first chunk
    ("garbage", f"bytes=0-{CHUNK - 1}"),
])
def test_clamp_range(hdr, expected):
    assert clamp_range(hdr) == expected


def test_diskcache_roundtrip_and_lru_eviction(tmp_path):
    import os, time
    c = DiskCache(tmp_path, max_bytes=250)
    c.put("a", b"x" * 100, "image/jpeg")
    c.put("b", b"y" * 100, "image/png")
    # make 'a' older, then touch 'b'... then reading 'a' makes it recent again
    now = time.time()
    os.utime(c._path("a"), (now - 100, now - 100))
    os.utime(c._path("b"), (now - 50, now - 50))
    assert c.get("a") == (b"x" * 100, "image/jpeg")      # hit refreshes recency of 'a'
    c.put("c", b"z" * 100, "image/jpeg")                 # over cap -> evict oldest = 'b'
    assert c.get("b") is None
    assert c.get("a") is not None and c.get("c") is not None
    assert c.total_bytes() <= 250


def test_diskcache_skips_oversized(tmp_path):
    c = DiskCache(tmp_path, max_bytes=10)
    c.put("big", b"x" * 100, "image/jpeg")
    assert c.get("big") is None
