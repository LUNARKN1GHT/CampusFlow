"""本机单用户会话接口。

MVP 不建立多人账户表。成功登录后颁发仅保存在进程内的随机会话令牌；退出会立即
从有效令牌集合移除。服务重启会使所有会话失效，符合本地开发阶段的边界。
"""

import secrets
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

SESSION_COOKIE = "campusflow_session"

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class SessionOut(BaseModel):
    username: str


def _valid_token(request: Request, token: str | None) -> bool:
    return token is not None and token in request.app.state.auth_tokens


def require_session(
    request: Request,
    campusflow_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> str:
    if not _valid_token(request, campusflow_session):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    return request.app.state.settings.local_username


@router.post("/login", response_model=SessionOut)
def login(payload: LoginInput, request: Request, response: Response) -> SessionOut:
    settings = request.app.state.settings
    username_matches = secrets.compare_digest(payload.username, settings.local_username)
    password_matches = secrets.compare_digest(payload.password, settings.local_password)
    if not (username_matches and password_matches):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")

    token = secrets.token_urlsafe(32)
    request.app.state.auth_tokens.add(token)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=60 * 60 * 12,
        path="/",
    )
    return SessionOut(username=settings.local_username)


@router.get("/session", response_model=SessionOut)
def get_session(username: Annotated[str, Depends(require_session)]) -> SessionOut:
    return SessionOut(username=username)


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    campusflow_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> None:
    if campusflow_session is not None:
        request.app.state.auth_tokens.discard(campusflow_session)
    response.delete_cookie(SESSION_COOKIE, path="/")
