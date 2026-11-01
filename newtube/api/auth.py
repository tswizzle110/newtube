"""Login / logout."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from ..app import Context
from ..auth import COOKIE, SESSION_SECONDS
from .deps import get_ctx

router = APIRouter(prefix="/api")


class LoginIn(BaseModel):
    password: str


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response,
                ctx: Context = Depends(get_ctx)) -> dict[str, bool]:
    wait = ctx.auth.lockout_remaining()
    if wait:
        raise HTTPException(429, f"too many attempts, try again in {wait}s",
                            headers={"Retry-After": str(wait)})
    if not ctx.auth.verify_password(body.password):
        raise HTTPException(401, "wrong password")
    response.set_cookie(COOKIE, ctx.auth.make_token(), max_age=SESSION_SECONDS, httponly=True,
                        samesite="strict", secure=request.url.scheme == "https", path="/")
    return {"ok": True}


@router.post("/logout")
async def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/status")
async def status(request: Request, ctx: Context = Depends(get_ctx)) -> dict[str, bool]:
    return {"authenticated": ctx.auth.check_token(request.cookies.get(COOKIE))}
