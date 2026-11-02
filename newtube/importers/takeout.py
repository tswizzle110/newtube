"""Read a Google Takeout and either describe it (inspect) or write it to the database (import).

Both use the SAME pass over the files (``read_takeout``); only the *sink* differs, so what `inspect`
reports is exactly what `import` would do. Files are recognised by their content, never by name.
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..db.queries.events import log_event
from .jsonstream import NotAnArrayOfObjects, iter_json_array
from .parse import (
    SKIP_REASONS, SearchEvent, WatchEvent, classify_playlist_name, parse_activity, parse_playlist_index,
    parse_playlist_items, parse_subscriptions, sniff_csv,
)
from .source import Member, open_takeout

# Things you can leave out of an import (--skip ...)
PARTS = ("watch", "search", "subscriptions", "likes", "watch-later", "playlists")
WATCH_PAYLOAD = '{"src": "takeout"}'          # constant: it is part of the de-duplication key


@dataclass
class Options:
    include_music: bool = False
    skip: set[str] = field(default_factory=set)
    likes: str | None = None                  # part of the playlist file name, to override detection
    watch_later: str | None = None


@dataclass
class FileInfo:
    name: str
    kind: str                                 # what we decided it is
    entries: int = 0
    note: str = ""


@dataclass
class Report:
    files: list[FileInfo] = field(default_factory=list)
    ignored: Counter = field(default_factory=Counter)             # file suffix -> count
    activity_entries: int = 0
    skipped: Counter = field(default_factory=Counter)              # reason -> count
    watches: int = 0
    searches: int = 0
    first_watch: int | None = None
    last_watch: int | None = None
    videos: set[str] = field(default_factory=set)
    channels: set[str] = field(default_factory=set)
    subscriptions: int = 0
    playlists: list[tuple[str, str, int]] = field(default_factory=list)      # (name, category, items)
    index_playlists: int = 0
    db: dict[str, int] = field(default_factory=dict)                         # what the database actually gained


class Sink(Protocol):
    def watch(self, ev: WatchEvent) -> None: ...
    def search(self, ev: SearchEvent) -> None: ...
    def subscription(self, channel_id: str, title: str) -> None: ...
    def playlist_item(self, category: str, name: str, video_id: str, ts: int | None) -> None: ...


class NullSink:
    """For `inspect`: records nothing."""
    def watch(self, ev: WatchEvent) -> None: ...
    def search(self, ev: SearchEvent) -> None: ...
    def subscription(self, channel_id: str, title: str) -> None: ...
    def playlist_item(self, category: str, name: str, video_id: str, ts: int | None) -> None: ...


def _describe(obj: Any, depth: int = 0) -> Any:
    """Structure of a value without its content: keys and types only (safe to paste into a bug report)."""
    if isinstance(obj, dict):
        return {k: _describe(v, depth + 1) for k, v in list(obj.items())[:12]} if depth < 3 else "{...}"
    if isinstance(obj, list):
        return [_describe(obj[0], depth + 1)] if obj and depth < 3 else "[...]"
    return type(obj).__name__


def _read_json(m: Member, sink: Sink, opts: Options, rep: Report, progress: Callable[[str], None] | None) -> None:
    info = FileInfo(m.name, "json")
    rep.files.append(info)
    kept = Counter()
    shape: Any = None
    try:
        with m.text() as fp:
            for entry in iter_json_array(fp):
                info.entries += 1
                rep.activity_entries += 1
                if shape is None:
                    shape = _describe(entry)
                r = parse_activity(entry, opts.include_music)
                if isinstance(r, WatchEvent):
                    kept["watch"] += 1
                    rep.watches += 1
                    rep.videos.add(r.video_id)
                    if r.channel_id:
                        rep.channels.add(r.channel_id)
                    rep.first_watch = r.ts if rep.first_watch is None else min(rep.first_watch, r.ts)
                    rep.last_watch = r.ts if rep.last_watch is None else max(rep.last_watch, r.ts)
                    if "watch" not in opts.skip:
                        sink.watch(r)
                elif isinstance(r, SearchEvent):
                    kept["search"] += 1
                    rep.searches += 1
                    if "search" not in opts.skip:
                        sink.search(r)
                else:
                    rep.skipped[r] += 1
                if progress and rep.activity_entries % 50_000 == 0:
                    progress(f"  ...{rep.activity_entries:,} history entries read")
    except NotAnArrayOfObjects:
        info.kind, info.note = "unrecognised", "JSON, but not a list of activity entries"
        if (m.size or 0) < 5_000_000:
            try:
                with m.text() as fp:
                    info.note += f"; structure: {json.dumps(_describe(json.load(fp)))[:300]}"
            except ValueError:
                pass
        return
    except json.JSONDecodeError as e:
        info.kind = "broken"
        info.note = (f"invalid JSON ({e.msg} at character {e.pos}); "
                     + (f"the {info.entries:,} entries before the damage were used" if info.entries else "nothing could be read"))
        return
    if kept:
        info.kind = "activity"
        info.note = ", ".join(f"{n:,} {k}" for k, n in kept.items())
    elif info.entries:
        info.kind = "unrecognised"
        info.note = f"{info.entries:,} entries, none usable; structure of the first: {json.dumps(shape)[:300]}"
    else:
        info.kind, info.note = "empty", "no entries"


def _category(m: Member, opts: Options) -> str:
    name = m.name.lower()
    for given, cat in ((opts.likes, "likes"), (opts.watch_later, "watch_later")):
        if given and given.lower() in name:
            return cat
    return classify_playlist_name(m.stem)


def _read_csv(m: Member, sink: Sink, opts: Options, rep: Report) -> None:
    with m.text() as fp:
        rows = [r for r in csv.reader(fp) if r]
    header, data = (rows[0], rows[1:]) if rows else ([], [])
    kind = sniff_csv(data)
    if kind == "unknown" and rows:                         # no header row at all
        kind2 = sniff_csv(rows)
        if kind2 != "unknown":
            kind, data, header = kind2, rows, []
    info = FileInfo(m.name, kind)
    rep.files.append(info)
    if kind == "subscriptions":
        subs = parse_subscriptions(data)
        info.entries = len(subs)
        rep.subscriptions += len(subs)
        if "subscriptions" not in opts.skip:
            for cid, title in subs:
                sink.subscription(cid, title)
    elif kind == "playlist_items":
        cat = _category(m, opts)
        name = re.sub(r"[-_]videos$", "", m.stem) or m.stem          # "Liked videos-videos" -> "Liked videos"
        items = parse_playlist_items(data)
        info.entries = len(items)
        info.note = {"likes": "liked videos", "watch_later": "watch later"}.get(cat, "other playlist")
        rep.playlists.append((name, cat, len(items)))
        part = {"likes": "likes", "watch_later": "watch-later", "other": "playlists"}[cat]
        if part not in opts.skip:
            for vid, ts in items:
                sink.playlist_item(cat, name, vid, ts)
    elif kind == "playlists":
        idx = parse_playlist_index(data)
        info.entries = len(idx)
        info.note = "the list of your playlists (their videos are in the other files)"
        rep.index_playlists += len(idx)
    else:
        info.kind = "unrecognised"
        info.entries = len(data)
        info.note = f"{len(header)} columns: {', '.join(header)[:200]}" if header else f"{len(data)} rows, no header"


def read_takeout(paths: list[str], sink: Sink, opts: Options | None = None,
                 progress: Callable[[str], None] | None = None) -> Report:
    opts = opts or Options()
    rep = Report()
    for m in open_takeout(paths):
        if m.suffix == ".json":
            _read_json(m, sink, opts, rep, progress)
        elif m.suffix == ".csv":
            _read_csv(m, sink, opts, rep)
        else:
            rep.ignored[m.suffix or "(no extension)"] += 1
    return rep


# ---------------------------------------------------------------------------- writing to the database


class DbSink:
    """Collects records and writes them in batches inside the caller's transaction. Existing rows are
    never overwritten (INSERT OR IGNORE), so real metadata fetched earlier is safe."""

    BATCH = 5000

    def __init__(self, conn: sqlite3.Connection, now: int | None = None) -> None:
        self.conn = conn
        self.now = now if now is not None else int(time.time())
        self._channels: dict[str, str | None] = {}
        self._videos: dict[str, tuple[str | None, str]] = {}
        self._events: list[tuple[int, str, str | None, str | None, str]] = []
        self._subs: dict[str, str] = {}
        # every number is present even when it is 0, so "nothing new" is reported rather than omitted
        self.stats: Counter = Counter(channels_new=0, videos_new=0, events_new=0, events_seen=0,
                                      subscriptions_new=0, subscriptions_seen=0)

    def _changes(self, sql: str, rows: list[tuple[Any, ...]]) -> int:
        before = self.conn.total_changes
        self.conn.executemany(sql, rows)
        return self.conn.total_changes - before

    def _flush(self) -> None:
        self.stats["channels_new"] += self._changes(
            "INSERT OR IGNORE INTO channels(id, title, created_at) VALUES (?,?,?)",
            [(cid, title or cid, self.now) for cid, title in self._channels.items()])
        self.stats["videos_new"] += self._changes(
            "INSERT OR IGNORE INTO videos(id, channel_id, title, first_seen_at, from_import) VALUES (?,?,?,?,1)",
            [(vid, ch, title, self.now) for vid, (ch, title) in self._videos.items()])
        self.stats["events_new"] += self._changes(
            "INSERT OR IGNORE INTO events(ts, type, video_id, channel_id, payload) VALUES (?,?,?,?,?)",
            self._events)
        self.stats["events_seen"] += len(self._events)
        self._channels.clear(); self._videos.clear(); self._events.clear()

    def _maybe_flush(self) -> None:
        if len(self._events) >= self.BATCH:
            self._flush()

    def _note_channel(self, channel_id: str | None, title: str | None) -> None:
        if channel_id and (channel_id not in self._channels or (title and not self._channels[channel_id])):
            self._channels[channel_id] = title

    def watch(self, ev: WatchEvent) -> None:
        self._note_channel(ev.channel_id, ev.channel_title)
        self._videos.setdefault(ev.video_id, (ev.channel_id, ev.title or ""))
        self._events.append((ev.ts, "import_watch", ev.video_id, ev.channel_id, WATCH_PAYLOAD))
        self._maybe_flush()

    def search(self, ev: SearchEvent) -> None:
        self._events.append((ev.ts, "import_search", None, None, json.dumps({"q": ev.query, "src": "takeout"}, sort_keys=True)))
        self._maybe_flush()

    def subscription(self, channel_id: str, title: str) -> None:
        self._subs[channel_id] = title

    def playlist_item(self, category: str, name: str, video_id: str, ts: int | None) -> None:
        self._videos.setdefault(video_id, (None, ""))
        kind = {"likes": "import_like", "watch_later": "import_watch_later"}.get(category, "import_playlist_add")
        payload = WATCH_PAYLOAD if category != "other" else json.dumps({"playlist": name, "src": "takeout"}, sort_keys=True)
        self._events.append((ts or 0, kind, video_id, None, payload))     # 0 = "time unknown", stable for de-duplication
        self._maybe_flush()

    def finish(self) -> None:
        self._flush()
        for cid, title in self._subs.items():
            self.stats["channels_new"] += self.conn.execute(
                "INSERT OR IGNORE INTO channels(id, title, created_at) VALUES (?,?,?)", (cid, title, self.now)).rowcount
            # The subscriptions file has the channel's full name: prefer it over the one the watch history gave
            # (or the bare id) for channels created by THIS import. Titles YouTube itself supplied are left alone.
            if title:
                self.conn.execute("UPDATE channels SET title=? WHERE id=? AND (created_at=? OR title=id) AND title<>?",
                                  (title, cid, self.now, title))
            cur = self.conn.execute("INSERT OR IGNORE INTO subscriptions(channel_id, subscribed_at) VALUES (?,?)", (cid, self.now))
            if cur.rowcount:
                log_event(self.conn, "subscribe", channel_id=cid, payload={"source": "takeout"}, ts=self.now)
                self.stats["subscriptions_new"] += 1
        self.stats["subscriptions_seen"] = len(self._subs)


def run_import(conn: sqlite3.Connection, paths: list[str], opts: Options | None = None, *, dry_run: bool = False,
               progress: Callable[[str], None] | None = None) -> Report:
    """All or nothing: one transaction. ``dry_run`` does everything and then rolls it back, so the
    numbers are exact but nothing is kept."""
    conn.execute("BEGIN")
    try:
        sink = DbSink(conn)
        rep = read_takeout(paths, sink, opts, progress)
        sink.finish()
        rep.db = dict(sink.stats)
        conn.execute("ROLLBACK" if dry_run else "COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return rep


# ---------------------------------------------------------------------------- the printed report


def _day(ts: int | None) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts)) if ts else "?"


def format_report(rep: Report, *, wrote: bool | None = None) -> str:
    """``wrote``: None = inspect (nothing is written), True = imported, False = dry run."""
    L: list[str] = []
    read = [f for f in rep.files]
    L.append(f"Read {len(read)} data files (JSON/CSV)"
             + (f"; ignored {sum(rep.ignored.values()):,} others ({', '.join(f'{k} ×{n}' for k, n in rep.ignored.most_common(6))})" if rep.ignored else ""))
    L.append("")
    if rep.activity_entries:
        kept = rep.watches + rep.searches
        L.append(f"History: {rep.activity_entries:,} entries, {kept:,} usable, {sum(rep.skipped.values()):,} skipped")
        L.append(f"  watches : {rep.watches:,}  ({len(rep.videos):,} different videos, {len(rep.channels):,} channels, "
                 f"{_day(rep.first_watch)} to {_day(rep.last_watch)})")
        L.append(f"  searches: {rep.searches:,}")
        for reason, n in rep.skipped.most_common():
            L.append(f"  skipped : {n:,} {SKIP_REASONS.get(reason, reason)}")
    else:
        L.append("History: none found (no watch-history / search-history JSON?)")
    L.append(f"Subscriptions: {rep.subscriptions:,} channels" if rep.subscriptions else "Subscriptions: none found")
    if rep.playlists:
        L.append("Playlists:")
        for name, cat, n in rep.playlists:
            L.append(f"  {n:>6,}  \"{name}\"  -> {({'likes': 'LIKES (positive signal)', 'watch_later': 'WATCH LATER', 'other': 'other playlist'})[cat]}")
        if not any(c == "likes" for _, c, _ in rep.playlists):
            L.append("  ! no liked-videos file recognised; if one of the above is it, pass --likes \"part of its file name\"")
        if not any(c == "watch_later" for _, c, _ in rep.playlists):
            L.append("  ! no watch-later file recognised; if one of the above is it, pass --watch-later \"part of its file name\"")
    odd = [f for f in rep.files if f.kind in ("unrecognised", "broken")]
    if odd:
        L.append("")
        L.append("Files I could not use:")
        for f in odd:
            L.append(f"  {f.name}: {f.note}")
    if wrote is not None:
        L.append("")
        d = rep.db
        verb = "Imported" if wrote else "Dry run (nothing was saved); an import would add"
        L.append(f"{verb}: {d.get('events_new', 0):,} new events of {d.get('events_seen', 0):,} "
                 f"({d.get('events_seen', 0) - d.get('events_new', 0):,} were already there), "
                 f"{d.get('videos_new', 0):,} new videos, {d.get('channels_new', 0):,} new channels, "
                 f"{d.get('subscriptions_new', 0):,} new subscriptions ({d.get('subscriptions_seen', 0):,} in the file)")
    return "\n".join(L)
