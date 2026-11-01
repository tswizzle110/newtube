"""Comments: cached, with a deliberately small reply budget.

yt-dlp can neither page nor target one thread (see Extractor.comments), so:
* top-level comments are fetched with a limit; "load more" re-fetches with a bigger one;
* replies exist only for the first MAX_REPLY_THREADS comments. Expanding comment #k fetches
  threads 1..k in one go and caches them all, so expanding an earlier thread later is free.
Top-level comments and replies are cached separately, so loading more comments never throws
away replies that were already fetched.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from typing import Any

from ..db.queries import cache
from ..extract.base import Comment, Extractor

CACHE_VERSION = 1
TTL = 3600
MAX_LIMIT = 100
MAX_REPLY_THREADS = 10
REPLIES_PER_THREAD = 20


class CommentsNotLoaded(Exception):
    """Replies were requested for a comment we haven't listed (load the comments first)."""


class RepliesUnavailable(Exception):
    """The comment is beyond the first MAX_REPLY_THREADS."""


def to_dict(c: Comment) -> dict[str, Any]:
    return dataclasses.asdict(c)


class CommentService:
    def __init__(self, conn: sqlite3.Connection, extractor: Extractor) -> None:
        self.conn, self.extractor = conn, extractor

    @staticmethod
    def _key(video_id: str, sort: str, suffix: str = "") -> str:
        return f"comments:v{CACHE_VERSION}:{video_id}:{sort}{suffix}"

    async def top(self, video_id: str, sort: str, limit: int) -> dict[str, Any]:
        limit = max(1, min(limit, MAX_LIMIT))
        key = self._key(video_id, sort)
        cached = cache.get(self.conn, key)
        if not cached or cached["limit"] < limit:
            page = await self.extractor.comments(video_id, sort, limit)
            cached = {"limit": limit, "total": page.total_estimate,
                      "comments": [to_dict(c) for c in page.comments if c.parent is None]}
            cache.put(self.conn, key, cached, TTL)
        have = len(cached["comments"])
        # More exist if we hold more than asked for, or the last fetch came back full (so it was
        # cut off by its limit rather than by running out of comments).
        has_more = have > limit or (have >= cached["limit"] and limit < MAX_LIMIT)
        return {"comments": cached["comments"][:limit], "has_more": has_more,
                "total_estimate": cached["total"], "max_reply_threads": MAX_REPLY_THREADS}

    async def replies(self, video_id: str, sort: str, comment_id: str) -> dict[str, Any]:
        cached = cache.get(self.conn, self._key(video_id, sort))
        ids = [c["id"] for c in cached["comments"]] if cached else []
        if comment_id not in ids:
            raise CommentsNotLoaded(comment_id)
        threads = ids.index(comment_id) + 1
        if threads > MAX_REPLY_THREADS:
            raise RepliesUnavailable(comment_id)
        rkey = self._key(video_id, sort, ":replies")
        store = cache.get(self.conn, rkey) or {"upto": 0, "by_parent": {}}
        if store["upto"] < threads:
            page = await self.extractor.comments(
                video_id, sort, threads, replies_for=threads, replies_per_thread=REPLIES_PER_THREAD)
            for c in page.comments:
                if c.parent:
                    store["by_parent"].setdefault(c.parent, []).append(to_dict(c))
                else:       # a thread we walked: record "no replies" explicitly
                    store["by_parent"].setdefault(c.id, [])
            store["upto"] = threads
            cache.put(self.conn, rkey, store, TTL)
        found = store["by_parent"].get(comment_id, [])
        return {"replies": found, "truncated": len(found) >= REPLIES_PER_THREAD}
