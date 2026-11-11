"""ログイン状態の確認と、ブラウザ GUI 用のログイン(API キー → HttpOnly クッキー)。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from . import audit, auth

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    api_key: str


def _me(mode: str, principal: auth.Principal | None) -> dict:
    if principal is None:
        return {"mode": mode, "authenticated": False, "name": None, "role": None, "source": None, "debug": False}
    # debug: 開発者・デバッグ向け(監視 HUD)が、この人に使えるか。GUI の出し分け用(判断はサーバー側)
    return {"mode": mode, "authenticated": True, "name": principal.name, "role": principal.role,
            "source": principal.source, "debug": auth.debug_available(principal.role)}


@router.get("/me")
def me(request: Request) -> dict:
    """GUI が最初に呼ぶ。未ログインでも 401 にせず、authenticated=false を返す。"""
    return _me(auth.auth_mode(), auth.current_principal(request))


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response) -> dict:
    if auth.auth_mode() == "off":
        return _me("off", auth.ANONYMOUS)
    key = body.api_key.strip()
    user = auth.find_by_key(key, auth.load_users())
    ip = request.client.host if request.client else None
    if user is None:
        audit.record("login_failed", method="POST", path="/api/auth/login", status=401, ip=ip)
        raise HTTPException(status_code=401, detail="API キーが正しくありません。")
    audit.record("login", actor=user["name"], role=user["role"], source="key", method="POST",
                 path="/api/auth/login", status=200, ip=ip)
    # JS から読めないクッキー。別サイトからは送られない(SameSite=Strict)。WebSocket(/ws/hud)のハンドシェイクにも付く。
    response.set_cookie(auth.COOKIE_NAME, key, httponly=True, samesite="strict",
                        secure=request.url.scheme == "https", path="/")
    return _me("required", auth.Principal(name=user["name"], role=user["role"], source="key"))


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response) -> None:
    principal = auth.current_principal(request) if auth.auth_mode() == "required" else None
    if principal is not None:
        audit.record("logout", actor=principal.name, role=principal.role, source=principal.source, method="POST",
                     path="/api/auth/logout", status=204, ip=request.client.host if request.client else None)
    response.delete_cookie(auth.COOKIE_NAME, path="/")


@router.get("/audit", dependencies=[auth.admin])
def audit_log(limit: int = 100) -> dict:
    """監査ログ(新しい順)。admin だけ。本文・キー・クッキーは記録されていない。"""
    return {"entries": audit.read_recent(max(1, min(limit, 500)))}
