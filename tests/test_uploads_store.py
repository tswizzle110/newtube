"""The stored upload list: replace, top up, order."""
import pytest

from newtube.db.connection import connect, migrate
from newtube.db.queries import uploads as up
from newtube.extract.base import VideoSummary

CH = "UC" + "a" * 22


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    return c


def vids(*specs):
    """(id, views) pairs, newest first."""
    return [VideoSummary(i, f"title {i}", CH, None, 60, v) for i, v in specs]


def ids(conn, sort, offset=0, limit=100):
    return [v["id"] for v in up.page(conn, CH, sort, offset, limit)]


def test_store_replaces_the_list_and_records_when(conn):
    up.store_scan(conn, CH, vids(("a", 1), ("b", 2), ("c", 3)), total=3, truncated=False, now=1000)
    up.store_scan(conn, CH, vids(("b", 2), ("d", 9)), total=2, truncated=False, now=2000)       # "a" and "c" were deleted
    assert ids(conn, "newest") == ["b", "d"] and up.count(conn, CH) == 2
    info = up.scan_info(conn, CH)
    assert (info["scanned_at"], info["topped_up_at"], info["total"], info["truncated"]) == (2000, 2000, 2, 0)


def test_the_three_orders(conn):
    up.store_scan(conn, CH, vids(("n1", 5), ("n2", 500), ("n3", None), ("n4", 500), ("n5", 20)), 5, False)
    assert ids(conn, "newest") == ["n1", "n2", "n3", "n4", "n5"]
    assert ids(conn, "oldest") == ["n5", "n4", "n3", "n2", "n1"]
    assert ids(conn, "popular") == ["n2", "n4", "n5", "n1", "n3"]        # ties: newer first; unknown views last
    assert ids(conn, "oldest", offset=3, limit=5) == ["n2", "n1"] and ids(conn, "popular", 1, 2) == ["n4", "n5"]


def test_paging_by_offset_covers_everything_once(conn):
    up.store_scan(conn, CH, vids(*[(f"v{i:03d}", i) for i in range(250)]), 250, False)
    seen = []
    for offset in range(0, 250, 60):
        seen += ids(conn, "oldest", offset, 60)
    assert len(seen) == 250 and len(set(seen)) == 250 and seen[0] == "v249" and seen[-1] == "v000"


def test_top_up_adds_new_videos_above_everything_and_refreshes_known_ones(conn):
    up.store_scan(conn, CH, vids(("o1", 10), ("o2", 20), ("o3", 30)), total=3, truncated=False, now=1000)
    page = [VideoSummary("new2", "t", CH, None, 9, 1), VideoSummary("new1", "t", CH, None, 9, 1),
            VideoSummary("o1", "renamed", CH, None, None, 999)]            # newest first; o1 gained views
    assert up.top_up(conn, CH, page, now=5000) == "ok"
    assert ids(conn, "newest") == ["new2", "new1", "o1", "o2", "o3"]
    assert ids(conn, "oldest")[-1] == "new2"
    got = {v["id"]: v for v in up.page(conn, CH, "newest", 0, 10)}
    assert got["o1"]["view_count"] == 999 and got["o1"]["title"] == "renamed" and got["o1"]["duration"] == 60   # None keeps the old duration
    info = up.scan_info(conn, CH)
    assert info["topped_up_at"] == 5000 and info["scanned_at"] == 1000 and info["total"] == 5


def test_repeated_top_ups_stay_in_order_and_never_duplicate(conn):
    up.store_scan(conn, CH, vids(("o1", 1)), 1, False)
    up.top_up(conn, CH, vids(("a", 1), ("o1", 1)))
    up.top_up(conn, CH, vids(("b", 1), ("a", 1), ("o1", 1)))
    up.top_up(conn, CH, vids(("b", 1), ("a", 1), ("o1", 1)))                 # nothing new
    assert ids(conn, "newest") == ["b", "a", "o1"] and up.count(conn, CH) == 3


def test_a_page_with_no_known_video_means_a_gap(conn):
    up.store_scan(conn, CH, vids(("o1", 1), ("o2", 2)), 2, False, now=1000)
    assert up.top_up(conn, CH, vids(*[(f"x{i}", 1) for i in range(100)]), now=9000) == "gap"
    assert ids(conn, "newest") == ["o1", "o2"]                               # nothing half-applied
    assert up.scan_info(conn, CH)["topped_up_at"] == 1000


def test_an_empty_page_is_fine(conn):
    up.store_scan(conn, CH, vids(("o1", 1)), 1, False, now=1000)
    assert up.top_up(conn, CH, [], now=3000) == "ok" and up.scan_info(conn, CH)["topped_up_at"] == 3000


def test_other_channels_are_untouched(conn):
    other = "UC" + "b" * 22
    up.store_scan(conn, CH, vids(("a", 1)), 1, False)
    up.store_scan(conn, other, [VideoSummary("z", "t", other, None, 1, 1)], 1, False)
    up.store_scan(conn, CH, vids(("c", 1)), 1, False)
    assert up.count(conn, other) == 1 and [v["id"] for v in up.page(conn, other, "newest", 0, 5)] == ["z"]


def test_stored_size_is_about_a_hundred_bytes_per_video(tmp_path):
    import os, random, string
    random.seed(3)
    c = connect(tmp_path / "size.db")
    migrate(c)
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    base = os.path.getsize(tmp_path / "size.db")
    words = [''.join(random.choices(string.ascii_lowercase, k=random.randint(3, 9))) for _ in range(2000)]   # varied, like real titles
    videos = [VideoSummary(''.join(random.choices(string.ascii_letters + string.digits + "_-", k=11)),
                           " ".join(random.choices(words, k=random.randint(4, 11)))[:90], CH, None,
                           random.randint(30, 3600), random.randint(100, 20_000_000)) for _ in range(3000)]
    up.store_scan(c, CH, videos, 3000, False)
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    per_video = (os.path.getsize(tmp_path / "size.db") - base) / 3000
    assert 60 < per_video < 200, per_video
