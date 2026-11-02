"""Big bounded ranges (a 4K segment can exceed 10 MiB) must arrive complete, not truncated."""
import re

import httpx
import pytest

from newtube.media.ranges import CHUNK, parse_range, split_range
from tests.test_api import FakeExtractor, _Body, make_client

TOTAL = 30 * 1024 * 1024
PATTERN = bytes(range(251))        # prime length: a misplaced chunk can't line up by accident


def piece(a: int, b: int) -> bytes:
    return (PATTERN * ((b + 1) // 251 + 2))[a:b + 1]


upstream_ranges: list[tuple[int, int]] = []


def handler(req: httpx.Request) -> httpx.Response:
    if "googlevideo.com" not in str(req.url):
        return httpx.Response(404)
    m = re.match(r"bytes=(\d+)-(\d+)", req.headers["range"])
    a, b = int(m.group(1)), min(int(m.group(2)), TOTAL - 1)
    upstream_ranges.append((a, b))
    return httpx.Response(206, stream=_Body(piece(a, b)), headers={
        "content-type": "video/webm", "content-range": f"bytes {a}-{b}/{TOTAL}", "content-length": str(b - a + 1)})


@pytest.fixture
def client(tmp_path):
    upstream_ranges.clear()
    with make_client(tmp_path, handler, FakeExtractor()) as (c, ctx):
        yield c


def get(client, rng):
    return client.get("/api/stream/aaaaaaaaaaa/18", headers={"Range": rng})


def test_a_range_of_25_mib_is_fetched_in_chunks_and_arrives_whole_and_in_order(client):
    a, n = 100, 25 * 1024 * 1024
    r = get(client, f"bytes={a}-{a + n - 1}")
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes {a}-{a + n - 1}/{TOTAL}" and int(r.headers["content-length"]) == n
    assert len(r.content) == n and r.content == piece(a, a + n - 1)              # every byte in the right place
    assert upstream_ranges == [(100, 100 + CHUNK - 1), (100 + CHUNK, 100 + 2 * CHUNK - 1), (100 + 2 * CHUNK, a + n - 1)]


def test_a_range_past_the_end_of_the_file_is_clamped_not_failed(client):
    r = get(client, f"bytes={TOTAL - 5}-{TOTAL + 5000}")
    assert r.status_code == 206 and r.content == piece(TOTAL - 5, TOTAL - 1)
    assert r.headers["content-range"] == f"bytes {TOTAL - 5}-{TOTAL - 1}/{TOTAL}"


def test_a_range_of_exactly_one_chunk_needs_one_request(client):
    r = get(client, f"bytes=0-{CHUNK - 1}")
    assert len(r.content) == CHUNK and len(upstream_ranges) == 1


def test_open_ended_requests_still_get_one_chunk_and_the_browser_asks_again(client):
    r = get(client, "bytes=1000-")
    assert r.status_code == 206 and len(r.content) == CHUNK and len(upstream_ranges) == 1
    assert r.headers["content-range"] == f"bytes 1000-{1000 + CHUNK - 1}/{TOTAL}"
    r2 = client.get("/api/stream/aaaaaaaaaaa/18")                                  # no Range header at all
    assert r2.status_code == 206 and len(r2.content) == CHUNK


def test_small_segment_ranges_are_unchanged(client):
    r = get(client, "bytes=500-1499")
    assert r.content == piece(500, 1499) and r.headers["content-range"] == f"bytes 500-1499/{TOTAL}"


@pytest.mark.parametrize("hdr,expected", [
    (None, (0, None)), ("", (0, None)), ("bytes=0-", (0, None)), ("bytes=100-199", (100, 199)), ("bytes=100-", (100, None)),
    ("bytes=-500", (None, 500)), ("bytes=0-1,5-9", (0, None)), ("garbage", (0, None)), ("bytes=9-3", (0, None))])
def test_parse_range(hdr, expected):
    assert parse_range(hdr) == expected


def test_split_range_covers_everything_exactly_once():
    parts = split_range(5, 5 + 25 * 1024 * 1024 - 1)
    assert parts[0][0] == 5 and parts[-1][1] == 5 + 25 * 1024 * 1024 - 1
    assert all(b - a + 1 <= CHUNK for a, b in parts)
    assert all(parts[i][1] + 1 == parts[i + 1][0] for i in range(len(parts) - 1))
    assert split_range(0, 0) == [(0, 0)]
