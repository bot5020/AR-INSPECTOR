"""Вход, выход, текущий пользователь."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from .. import auth

router = APIRouter(prefix="/api/auth", tags=["вход"])


@router.get("/me")
def whoami(request: Request) -> dict[str, Any]:
    return {"user": auth.current(request)}


@router.post("/login")
async def login(request: Request, payload: dict[str, Any] = Body(...)) -> JSONResponse:
    username, password = payload.get("username"), payload.get("password")
    if not isinstance(username, str) or not isinstance(password, str) or len(username) > 100 or len(password) > 200:
        raise HTTPException(400, "Введите имя пользователя и пароль.")
    ip = request.client.host if request.client else "?"
    auth.limiter.check(ip, username)
    try:
        token, user = await run_in_threadpool(auth.login, username, password)   # scrypt — не держим цикл событий
    except HTTPException:
        auth.limiter.failed(ip, username)
        raise
    auth.limiter.succeeded(ip, username)
    response = JSONResponse({"user": user})
    response.set_cookie(auth.COOKIE, token, httponly=True, secure=request.url.scheme == "https",
                        samesite="strict", max_age=12 * 3600)
    return response


@router.post("/logout")
def logout(request: Request) -> JSONResponse:
    auth.logout(request)
    response = JSONResponse({"ok": True})
    response.delete_cookie(auth.COOKIE)
    return response
