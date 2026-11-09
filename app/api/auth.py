"""登录相关接口与鉴权依赖：/api/auth/login、/logout、/me。

鉴权方式：登录成功后下发 HttpOnly Cookie，服务端在 SQLite 里存会话。
不使用 JWT —— 这样可以在任何时候（改密码、停用账号）立刻让会话失效。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.config import settings
from app.users import (
    SESSION_COOKIE,
    create_session,
    delete_session,
    get_session_user,
    has_any_user,
    verify_login,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    """登录请求体。"""

    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=200)


def current_user(session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE)) -> Optional[str]:
    """读取当前登录用户；未登录返回 None（用于 /me 这类允许匿名访问的接口）。"""
    return get_session_user(session)


def require_user(user: Optional[str] = Depends(current_user)) -> str:
    """要求已登录，否则 401。用于需要登录才能使用的接口。"""
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


@router.post("/login")
def login(request: LoginRequest, response: Response) -> dict[str, Any]:
    """校验用户名密码，成功则下发会话 Cookie。"""
    if not has_any_user():
        raise HTTPException(status_code=503, detail="系统还没有任何用户，请先在服务器上创建账号")

    ok, reason = verify_login(request.username, request.password)
    if not ok:
        # 失败原因只告诉用户「被锁了」或「用户名或密码错误」，不区分账号是否存在
        logger.info("登录失败：user=%s reason=%s", request.username, reason)
        raise HTTPException(status_code=401, detail=reason or "用户名或密码错误")

    token, expires_at = create_session(request.username)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=settings.session_ttl_days * 86400,
        httponly=True,               # JS 读不到，防 XSS 窃取
        samesite="lax",              # 防 CSRF；同一站点导航正常携带
        secure=settings.cookie_secure,
        path="/",
    )
    logger.info("登录成功：user=%s", request.username)
    return {"username": request.username, "expires_at": expires_at}


@router.post("/logout")
def logout(response: Response, session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE)) -> dict[str, bool]:
    """退出登录：服务端删除会话，同时清掉 Cookie。"""
    delete_session(session)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"logged_out": True}


@router.get("/me")
def me(user: Optional[str] = Depends(current_user)) -> dict[str, Any]:
    """返回当前登录状态，前端启动时用它判断是否需要弹登录框。"""
    return {"logged_in": user is not None, "username": user}
