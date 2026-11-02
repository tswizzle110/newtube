"""Video metadata for the watch page. Never returns raw googlevideo URLs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..app import Context
from ..extract.base import AgeRestricted, BotCheck, ExtractionError, VideoUnavailable
from ..db.queries import mylists
from ..media.dash import audio_tracks
from .deps import get_ctx, valid_video_id

router = APIRouter(prefix="/api")


@router.get("/video/{video_id}")
async def video(video_id: str, ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    valid_video_id(video_id)
    try:
        v = await ctx.videos.get(video_id)
    except AgeRestricted as e:
        raise HTTPException(451, f"age-restricted (not supported yet): {e}") from e
    except VideoUnavailable as e:
        raise HTTPException(404, str(e)) from e
    except BotCheck as e:
        raise HTTPException(503, f"YouTube bot check: {e}") from e
    except ExtractionError as e:
        raise HTTPException(502, str(e)) from e
    prog = ctx.conn.execute("SELECT position, completed FROM video_progress WHERE video_id=?",
                            (video_id,)).fetchone()
    lists_state = mylists.membership(ctx.conn, video_id)
    return {
        "id": v.id, "title": v.title, "description": v.description,
        "channel_id": v.channel_id, "channel_title": v.channel_title,
        "duration": v.duration, "published_at": v.published_at, "view_count": v.view_count,
        "is_live": v.is_live, "tags": v.tags,
        "chapters": [{"start": c.start, "title": c.title} for c in v.chapters],
        "audio_tracks": [{"lang": a.lang, "name": a.name, "original": a.original}
                         for a in audio_tracks(v.formats)],
        "captions": [{"lang": c.lang, "name": c.name, "auto": c.auto} for c in v.captions],
        "formats": [{"format_id": f.format_id, "height": f.height, "fps": f.fps, "ext": f.ext,
                     "vcodec": f.vcodec, "acodec": f.acodec, "muxed": f.is_muxed,
                     "tbr": f.tbr} for f in v.formats],
        "resume_position": prog["position"] if prog and not prog["completed"] else 0,
        **lists_state,
    }
