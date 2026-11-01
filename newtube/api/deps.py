"""Shared request helpers."""

from __future__ import annotations

import re

from fastapi import HTTPException, Request

from ..app import Context

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
PLAYLIST_ID = re.compile(r"^[A-Za-z0-9_-]{13,64}$")


def get_ctx(request: Request) -> Context:
    return request.app.state.ctx


def valid_video_id(video_id: str) -> str:
    if not VIDEO_ID.match(video_id):
        raise HTTPException(400, "bad video id")
    return video_id


def valid_channel_id(channel_id: str) -> str:
    if not CHANNEL_ID.match(channel_id):
        raise HTTPException(400, "bad channel id")
    return channel_id


def valid_playlist_id(playlist_id: str) -> str:
    if not PLAYLIST_ID.match(playlist_id):
        raise HTTPException(400, "bad playlist id")
    return playlist_id
