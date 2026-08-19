from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from chainalpha.auth.service import AuthError, AuthService, CurrentUser


load_dotenv(Path(__file__).resolve().parents[3] / ".env")

router = APIRouter(prefix="/api/auth", tags=["auth"])
auth_service = AuthService()


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=40)
    password: str = Field(..., min_length=8, max_length=256)
    invite_code: str = Field(..., min_length=1, max_length=128)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=40)
    password: str = Field(..., min_length=1, max_length=256)


class CreateInviteRequest(BaseModel):
    code: str | None = Field(default=None, max_length=128)
    label: str = Field(default="", max_length=100)
    max_uses: int = Field(default=1, ge=1, le=10000)
    expires_at: str | None = Field(default=None, max_length=40)


def auth_enabled() -> bool:
    return os.getenv("AUTH_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


def current_user(chainalpha_session: Annotated[str | None, Cookie()] = None) -> CurrentUser | None:
    if not auth_enabled():
        return CurrentUser(id=0, username="local", role="admin")
    return auth_service.read_session(chainalpha_session)


def require_user(user: Annotated[CurrentUser | None, Depends(current_user)]) -> CurrentUser:
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    return user


def require_admin(user: Annotated[CurrentUser, Depends(require_user)]) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user


@router.get("/config")
def config() -> dict:
    return {"enabled": auth_enabled(), "registration": "invite_only"}


@router.get("/me")
def me(user: Annotated[CurrentUser | None, Depends(current_user)]) -> dict:
    return {"authenticated": user is not None, "user": user.to_dict() if user else None, "auth_enabled": auth_enabled()}


@router.post("/register")
def register(payload: RegisterRequest, response: Response) -> dict:
    if not auth_enabled():
        raise HTTPException(status_code=400, detail="当前未启用认证")
    try:
        user = auth_service.register(username=payload.username, password=payload.password, invite_code=payload.invite_code)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _set_cookie(response, auth_service.issue_session(user))
    return {"user": user.to_dict()}


@router.post("/login")
def login(payload: LoginRequest, response: Response) -> dict:
    if not auth_enabled():
        raise HTTPException(status_code=400, detail="当前未启用认证")
    try:
        user = auth_service.authenticate(username=payload.username, password=payload.password)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    _set_cookie(response, auth_service.issue_session(user))
    return {"user": user.to_dict()}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(auth_service.cookie_name, path="/")
    return {"ok": True}


@router.get("/invites")
def list_invites(_: Annotated[CurrentUser, Depends(require_admin)]) -> dict:
    return {"invites": auth_service.list_invites()}


@router.post("/invites")
def create_invite(payload: CreateInviteRequest, user: Annotated[CurrentUser, Depends(require_admin)]) -> dict:
    try:
        invite = auth_service.create_invite(
            created_by=user,
            code=payload.code,
            label=payload.label,
            max_uses=payload.max_uses,
            expires_at=payload.expires_at,
        )
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"invite": invite}


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        auth_service.cookie_name,
        token,
        httponly=True,
        samesite="lax",
        secure=os.getenv("AUTH_COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes"},
        max_age=auth_service.session_days * 24 * 60 * 60,
        path="/",
    )
