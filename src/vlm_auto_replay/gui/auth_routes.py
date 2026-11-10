"""ログイン状態の確認と、ブラウザ GUI 用のログイン(API キー → HttpOnly クッキー)。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from . import auth

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    api_key: str


def _me(mode: str, principal: auth.Principal | None) -> dict:
    if principal is None:
        return {"mode": mode, "authenticated": False, "name": None, "role": None, "source": None}
    return {"mode": mode, "authenticated": True, "name": principal.name, "role": principal.role,
            "source": principal.source}


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
    if user is None:
        raise HTTPException(status_code=401, detail="API キーが正しくありません。")
    # JS から読めないクッキー。別サイトからは送られない(SameSite=Strict)。WebSocket(/ws/hud)のハンドシェイクにも付く。
    response.set_cookie(auth.COOKIE_NAME, key, httponly=True, samesite="strict",
                        secure=request.url.scheme == "https", path="/")
    return _me("required", auth.Principal(name=user["name"], role=user["role"], source="key"))


@router.post("/logout", status_code=204)
def logout(response: Response) -> None:
    response.delete_cookie(auth.COOKIE_NAME, path="/")
