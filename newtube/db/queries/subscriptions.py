"""Channels, subscriptions, RSS-derived videos and the chronological feed."""

from __future__ import annotations

import sqlite3
import time

from .events import log_event
from ...extract.base import ChannelInfo, VideoSummary
from ...extract.rss import Feed


def upsert_channel(conn: sqlite3.Connection, ch: ChannelInfo) -> None:
    conn.execute(
        "INSERT INTO channels(id, title, handle, avatar_url, description, created_at)"
        " VALUES (?,?,?,?,?,?)"
        " ON CONFLICT(id) DO UPDATE SET title=excluded.title,"
        "  handle=COALESCE(excluded.handle, handle),"
        "  avatar_url=COALESCE(excluded.avatar_url, avatar_url),"
        "  description=COALESCE(excluded.description, description)",
        (ch.id, ch.title, ch.handle, ch.avatar_url, ch.description, int(time.time())),
    )


def subscribe(conn: sqlite3.Connection, ch: ChannelInfo, *, source: str = "manual") -> bool:
    """Returns False if already subscribed."""
    upsert_channel(conn, ch)
    cur = conn.execute("INSERT OR IGNORE INTO subscriptions VALUES (?, ?)", (ch.id, int(time.time())))
    if cur.rowcount:
        log_event(conn, "subscribe", channel_id=ch.id, payload={"source": source})
    return bool(cur.rowcount)


def unsubscribe(conn: sqlite3.Connection, channel_id: str) -> bool:
    cur = conn.execute("DELETE FROM subscriptions WHERE channel_id=?", (channel_id,))
    if cur.rowcount:
        log_event(conn, "unsubscribe", channel_id=channel_id)
    return bool(cur.rowcount)


def list_subscriptions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT c.*, s.subscribed_at FROM subscriptions s JOIN channels c ON c.id = s.channel_id"
        " ORDER BY lower(c.title)"
    ).fetchall()


def store_feed(conn: sqlite3.Connection, feed: Feed) -> int:
    """Insert new videos from an RSS poll. Never clobbers fields the extractor filled in.
    Returns the number of videos that were new."""
    now = int(time.time())
    new = 0
    conn.execute("BEGIN")
    try:
        for e in feed.entries:
            is_new = conn.execute("SELECT 1 FROM videos WHERE id=?", (e.video_id,)).fetchone() is None
            conn.execute(
                "INSERT INTO videos(id, channel_id, title, description, published_at,"
                " view_count, thumbnail_url, first_seen_at) VALUES (?,?,?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET view_count=excluded.view_count,"
                "  title=excluded.title,"
                "  published_at=COALESCE(published_at, excluded.published_at)",
                (e.video_id, feed.channel_id, e.title, e.description, e.published_at,
                 e.view_count, e.thumbnail_url, now),
            )
            new += is_new
        conn.execute("UPDATE channels SET rss_checked_at=?, feed_seeded=1 WHERE id=?", (now, feed.channel_id))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return new


def store_discovered(conn: sqlite3.Connection, channel_id: str, videos: list[VideoSummary],
                     now: int | None = None) -> int:
    """Record videos found on a channel's page when RSS is unavailable. Returns how many NEW,
    feed-visible videos that produced.

    The page lists newest first but has no upload dates. So:
    * if this channel's uploads were already read once (``feed_seeded``), anything unseen is genuinely new: date it by when
      we first saw it (``now - position`` keeps their order) and it joins the feed;
    * if we know nothing about the channel yet, the list is just its backlog: remember the ids (so
      they are not mistaken for new uploads later) but leave them undated, which keeps them out of
      the chronological feed. Opening one fills in its real date."""
    now = now if now is not None else int(time.time())
    row = conn.execute("SELECT feed_seeded FROM channels WHERE id=?", (channel_id,)).fetchone()
    had_history = bool(row and row["feed_seeded"])
    new_visible = 0
    conn.execute("BEGIN")
    try:
        for position, v in enumerate(videos):
            if conn.execute("SELECT 1 FROM videos WHERE id=?", (v.id,)).fetchone():
                conn.execute("UPDATE videos SET duration=COALESCE(duration, ?) WHERE id=?",
                             (v.duration, v.id))
                continue
            published = now - position if had_history else None
            conn.execute(
                "INSERT INTO videos(id, channel_id, title, published_at, duration, view_count,"
                " first_seen_at) VALUES (?,?,?,?,?,?,?)",
                (v.id, channel_id, v.title, published, v.duration, v.view_count, now))
            new_visible += published is not None
        conn.execute("UPDATE channels SET rss_checked_at=?, feed_seeded=1 WHERE id=?", (now, channel_id))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return new_visible


def due_for_poll(conn: sqlite3.Connection, interval: int, now: int | None = None) -> list[str]:
    now = now if now is not None else int(time.time())
    rows = conn.execute(
        "SELECT c.id FROM subscriptions s JOIN channels c ON c.id=s.channel_id"
        " WHERE c.rss_checked_at IS NULL OR c.rss_checked_at <= ?"
        " ORDER BY c.rss_checked_at IS NOT NULL, c.rss_checked_at",
        (now - interval,),
    ).fetchall()
    return [r["id"] for r in rows]


def get_feed(conn: sqlite3.Connection, *, limit: int = 60,
             before: int | None = None) -> list[sqlite3.Row]:
    sql = ("SELECT v.*, c.title AS channel_title, c.avatar_url AS channel_avatar,"
           "  p.position AS resume_position, p.completed AS completed"
           " FROM videos v JOIN subscriptions s ON s.channel_id = v.channel_id"
           " JOIN channels c ON c.id = v.channel_id"
           " LEFT JOIN video_progress p ON p.video_id = v.id"
           " WHERE v.published_at IS NOT NULL")
    args: list[object] = []
    if before is not None:
        sql += " AND v.published_at < ?"
        args.append(before)
    sql += " ORDER BY v.published_at DESC LIMIT ?"
    args.append(limit)
    return conn.execute(sql, args).fetchall()
