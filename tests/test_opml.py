"""OPML: subscriptions out and in, without asking YouTube anything, and refusing anything dangerous."""
import pytest

from newtube.db.queries import subscriptions as subs
from newtube.extract.base import ChannelInfo
from newtube.importers import opml
from tests.test_api import env  # noqa: F401  (a fixture)

A, B = "UC" + "a" * 22, "UC" + "b" * 22


def test_export_then_import_gives_the_same_channels_including_awkward_names():
    channels = [(A, 'Rock & Roll "Live" <Best>'), (B, "Ünïcode ☕ channel")]
    assert opml.parse_opml(opml.export_opml(channels)) == channels


def test_other_feeds_and_duplicates_are_ignored_and_names_fall_back():
    text = f"""<opml><body><outline text="x">
      <outline title="Alpha" xmlUrl="https://www.youtube.com/feeds/videos.xml?channel_id={A}"/>
      <outline title="Alpha again" xmlUrl="https://www.youtube.com/feeds/videos.xml?channel_id={A}"/>
      <outline text="A blog" xmlUrl="https://example.org/feed.xml"/>
      <outline xmlUrl="https://www.youtube.com/feeds/videos.xml?channel_id={B}"/>
      <outline title="Bad id" xmlUrl="https://www.youtube.com/feeds/videos.xml?channel_id=UCshort"/>
    </outline></body></opml>"""
    assert opml.parse_opml(text) == [(A, "Alpha"), (B, B)]


@pytest.mark.parametrize("text,why", [
    ("not xml at all", "readable"),
    ("<opml><body></body></opml>", "no YouTube channels"),
    ('<!DOCTYPE lolz [<!ENTITY a "aaaa">]><opml><body><outline xmlUrl="x"/></body></opml>', "entities"),
    ("<opml>" + "x" * (opml.MAX_BYTES + 10) + "</opml>", "too large"),
])
def test_unreadable_empty_dangerous_and_huge_files_are_refused(text, why):
    with pytest.raises(opml.BadOpml, match=why):
        opml.parse_opml(text)


def test_the_apis_round_trip_with_no_request_to_youtube(env):
    client, ctx, ex, seen = env
    subs.subscribe(ctx.conn, ChannelInfo(A, "Alpha"))
    r = client.get("/api/subscriptions/export.opml")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"] and A in r.text
    subs.unsubscribe(ctx.conn, A)
    calls = ex.calls
    out = client.post("/api/subscriptions/import", json={"opml": r.text}).json()
    assert out == {"found": 1, "added": 1, "already": 0} and ex.calls == calls                  # the extractor was never asked
    assert [c["id"] for c in client.get("/api/subscriptions").json()] == [A]
    assert client.post("/api/subscriptions/import", json={"opml": r.text}).json() == {"found": 1, "added": 0, "already": 1}
    assert client.post("/api/subscriptions/import", json={"opml": "junk"}).status_code == 400
