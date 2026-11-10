"""gui/auth.py — 認証と役割(任意。既定は off)。VisualRegressionQATool の app/auth.py と同じ作り(2026-11-10 に移植)。

2 つの方式に対応する(両方を同時に有効にできる)。

1. **API キー**: ``X-API-Key`` ヘッダー(または ``Authorization: Bearer``)。ブラウザの GUI は、ログインで
   HttpOnly・SameSite=Strict のクッキーに入れる(``<img src>``・WebSocket はヘッダーを付けられないため)。
   キーは**ハッシュだけ**を JSON に保存する(``VLM_AUTH_FILE``。既定 ``~/.vlm_auto_replay/auth_users.json``)。
2. **Cloudflare Access**: ``Cf-Access-Jwt-Assertion`` の JWT を検証し、メールアドレスで登録済みユーザーに対応づける
   (ProductionTracker の ``cf_access.py`` と同じ守り方)。``VLM_CF_ACCESS_TEAM_DOMAIN`` と ``VLM_CF_ACCESS_AUD`` を設定したときだけ有効。

役割(上が下を含む):
    viewer   見るだけ(状態・履歴・観測・/ws/hud)
    operator ゴールの分解、実行の開始・停止(**端末へ実際にキー入力・クリックを送る**)
    admin    スキルの追加・削除、入力バックエンド(HID)の変更

モード(``VLM_AUTH``):
    off      認証なし(既定・従来どおり)。Host / Origin のガード(app/security.py)だけが効く。全員 admin 扱い。
    required 認証を必須にする。**ユーザーが 1 人もいなければ起動を止める**(設定ミスで素通しにしない=fail-closed)。

ユーザーの管理:
    python -m vlm_auto_replay.gui.auth add --name alice --role admin     # キーを 1 度だけ表示
    python -m vlm_auto_replay.gui.auth add --name bob --role viewer --email bob@example.com --no-key   # Access 用(キーなし)
    python -m vlm_auto_replay.gui.auth list
    python -m vlm_auto_replay.gui.auth remove --name alice
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import secrets
import sys
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, HTTPException
from starlette.requests import HTTPConnection

logger = logging.getLogger(__name__)

AUTH_ENV = "VLM_AUTH"
AUTH_FILE_ENV = "VLM_AUTH_FILE"
TEAM_DOMAIN_ENV = "VLM_CF_ACCESS_TEAM_DOMAIN"
AUD_ENV = "VLM_CF_ACCESS_AUD"

ROLES = ("viewer", "operator", "admin")
COOKIE_NAME = "vlm_key"
ACCESS_HEADER = "cf-access-jwt-assertion"
KEY_PREFIX = "vlm_"
JWKS_LIFESPAN_SECONDS = 3600

DEFAULT_AUTH_FILE = Path.home() / ".vlm_auto_replay" / "auth_users.json"


@dataclass(frozen=True)
class Principal:
    name: str
    role: str
    source: str  # "key" | "access" | "anonymous"


ANONYMOUS = Principal(name="anonymous", role="admin", source="anonymous")


# ── 設定 ────────────────────────────────────────────────────────────────────────
def auth_mode() -> str:
    mode = os.environ.get(AUTH_ENV, "off").strip().lower()
    if mode not in ("off", "required"):
        raise RuntimeError(f"{AUTH_ENV} は off か required にしてください(現在: {mode!r})。")
    return mode


def auth_file() -> Path:
    raw = os.environ.get(AUTH_FILE_ENV, "").strip()
    return Path(raw) if raw else DEFAULT_AUTH_FILE


def access_enabled() -> bool:
    return bool(team_domain() and os.environ.get(AUD_ENV, "").strip())


def team_domain() -> str:
    raw = os.environ.get(TEAM_DOMAIN_ENV, "").strip()
    return raw.removeprefix("https://").removeprefix("http://").rstrip("/")


def role_rank(role: str) -> int:
    return ROLES.index(role) if role in ROLES else -1


# ── ユーザーファイル(キーはハッシュだけ)──────────────────────────────────────────
def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def generate_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


_cache: dict[str, tuple[float, list[dict]]] = {}


def load_users(path: Path | None = None) -> list[dict]:
    path = path or auth_file()
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return []
    cached = _cache.get(str(path))
    if cached and cached[0] == mtime:
        return cached[1]
    data = json.loads(path.read_text(encoding="utf-8"))
    users = [u for u in data.get("users", []) if u.get("role") in ROLES and u.get("name")]
    _cache[str(path)] = (mtime, users)
    return users


def save_users(users: list[dict], path: Path | None = None) -> None:
    path = path or auth_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({"users": users}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass  # Windows など
    tmp.replace(path)
    _cache.pop(str(path), None)


def add_user(name: str, role: str, *, email: str | None = None, with_key: bool = True,
             path: Path | None = None) -> str | None:
    """ユーザーを足す。キーを作ったときは**そのキーを返す(保存されるのはハッシュだけ)**。"""
    if role not in ROLES:
        raise ValueError(f"role は {', '.join(ROLES)} のどれかにしてください。")
    name = name.strip()
    if not name:
        raise ValueError("name は必須です。")
    if not with_key and not email:
        raise ValueError("キーを作らないなら、Cloudflare Access 用に --email が要ります。")
    users = load_users(path)
    if any(u["name"] == name for u in users):
        raise ValueError(f"{name!r} は既にいます。")
    if email and any((u.get("email") or "").lower() == email.strip().lower() for u in users):
        raise ValueError(f"{email!r} は既に登録されています。")
    entry: dict = {"name": name, "role": role, "created_at": int(time.time())}
    key = None
    if with_key:
        key = generate_key()
        entry["key_sha256"] = hash_key(key)
    if email:
        entry["email"] = email.strip()
    save_users([*users, entry], path)
    return key


def remove_user(name: str, path: Path | None = None) -> bool:
    users = load_users(path)
    kept = [u for u in users if u["name"] != name]
    if len(kept) == len(users):
        return False
    save_users(kept, path)
    return True


def find_by_key(key: str, users: list[dict]) -> dict | None:
    """全ユーザーを同じ手順で比べる(一致した時点で止めない)。ハッシュ同士の比較は定数時間。"""
    digest = hash_key(key)
    found = None
    for u in users:
        stored = u.get("key_sha256")
        if stored and hmac.compare_digest(stored, digest):
            found = u
    return found


# ── Cloudflare Access ─────────────────────────────────────────────────────────
@lru_cache(maxsize=4)
def _jwks_client(domain: str):
    import jwt

    return jwt.PyJWKClient(f"https://{domain}/cdn-cgi/access/certs", cache_keys=True,
                           lifespan=JWKS_LIFESPAN_SECONDS, timeout=5)


def _signing_key(token: str):
    """トークンの kid に合う公開鍵。テストではここを差し替える(ネットワークを使わない)。"""
    return _jwks_client(team_domain()).get_signing_key_from_jwt(token).key


def verify_access_token(token: str) -> dict:
    import jwt

    try:
        key = _signing_key(token)
        return jwt.decode(
            token,
            key,
            algorithms=["RS256"],  # none / HS256 などは受け付けない
            audience=os.environ.get(AUD_ENV, "").strip(),
            issuer=f"https://{team_domain()}",
            options={"require": ["exp", "iat", "iss", "aud"]},
        )
    except jwt.PyJWTError as exc:
        # 理由(期限切れ・署名の不一致・鍵が取れない)は詳しく返さない。ログにだけ残す。
        logger.warning("Cloudflare Access の JWT を拒否しました: %s", type(exc).__name__)
        raise HTTPException(401, "Cloudflare Access の認証情報を確認できません。") from None


def principal_from_access(claims: dict, users: list[dict]) -> Principal:
    email = (claims.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(401, "認証情報にメールアドレスがありません。")
    matches = [u for u in users if (u.get("email") or "").strip().lower() == email]
    if len(matches) > 1:
        raise HTTPException(403, "このメールアドレスのユーザーが複数います。管理者に連絡してください。")
    if not matches:
        # 自動では作らない。Access を通っただけでは、このツールを使う権限にならない。
        raise HTTPException(403, "このツールを使う権限がありません。管理者に登録を依頼してください。")
    return Principal(name=matches[0]["name"], role=matches[0]["role"], source="access")


# ── リクエストから利用者を決める ───────────────────────────────────────────────────
def extract_key(request: HTTPConnection) -> str | None:
    key = request.headers.get("x-api-key")
    if key:
        return key.strip()
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(COOKIE_NAME)


def authenticate(request: HTTPConnection) -> Principal | None:
    """利用者を返す。資格情報が無ければ None、あっても不正なら 401/403 を投げる。"""
    users = load_users()
    if access_enabled():
        token = request.headers.get(ACCESS_HEADER)
        if token:
            return principal_from_access(verify_access_token(token), users)
    key = extract_key(request)
    if key:
        user = find_by_key(key, users)
        if user is None:
            raise HTTPException(401, "API キーが正しくありません。")
        return Principal(name=user["name"], role=user["role"], source="key")
    return None


def current_principal(request: HTTPConnection) -> Principal | None:
    """off のときは常に ANONYMOUS(admin 扱い)。required で資格情報が無ければ None。"""
    if auth_mode() == "off":
        return ANONYMOUS
    return authenticate(request)


def require_role(minimum: str):
    """FastAPI の依存。``dependencies=[Depends(require_role("operator"))]`` のように使う。"""
    if minimum not in ROLES:
        raise ValueError(minimum)

    def dependency(request: HTTPConnection) -> Principal:
        principal = current_principal(request)
        if principal is None:
            raise HTTPException(401, "認証が必要です。X-API-Key を付けるか、ログインしてください。",
                                headers={"WWW-Authenticate": "ApiKey"})
        request.state.principal = principal  # 権限不足でも、誰が試したかを監査ログに残すため、判定の前に載せる
        if role_rank(principal.role) < role_rank(minimum):
            logger.warning("権限不足: %s(%s)が %s を要求", principal.name, principal.role, request.url.path)
            raise HTTPException(403, f"この操作には {minimum} 以上の権限が必要です(あなたは {principal.role})。")
        request.state.principal = principal
        return principal

    return dependency


# 使い回し用
viewer = Depends(require_role("viewer"))
operator = Depends(require_role("operator"))
admin = Depends(require_role("admin"))


def principal_for_websocket(websocket: HTTPConnection, minimum: str = "viewer") -> Principal | None:
    """WebSocket のハンドシェイク(ヘッダー・クッキー)で認証する。通せなければ None(呼び出し側が close(1008))。

    HTTP の依存(require_role)は、失敗時に HTTP の応答を返す作りなので WebSocket には使わない。
    """
    try:
        principal = current_principal(websocket)
    except HTTPException:
        return None
    if principal is None or role_rank(principal.role) < role_rank(minimum):
        return None
    return principal


def ensure_auth_configured() -> None:
    """required なのに使えるユーザーが居なければ、起動を止める(fail-closed)。"""
    if auth_mode() == "off":
        logger.warning("認証は無効です(%s=off)。複数人で使うなら %s=required にしてください。", AUTH_ENV, AUTH_ENV)
        return
    if not load_users():
        raise RuntimeError(
            f"{AUTH_ENV}=required ですが、ユーザーが 1 人もいません({auth_file()})。"
            "`python -m vlm_auto_replay.gui.auth add --name <名前> --role admin` で作ってください。"
        )


# ── CLI ───────────────────────────────────────────────────────────────────────
def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m vlm_auto_replay.gui.auth", description="VLMAutoReplayTool GUI のユーザー(API キー・役割)の管理")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_add = sub.add_parser("add", help="ユーザーを足す(API キーは 1 度だけ表示)")
    p_add.add_argument("--name", required=True)
    p_add.add_argument("--role", required=True, choices=ROLES)
    p_add.add_argument("--email", help="Cloudflare Access のメールアドレス")
    p_add.add_argument("--no-key", action="store_true", help="API キーを作らない(Access だけで使う人)")
    sub.add_parser("list", help="ユーザーの一覧(キーは出さない)")
    p_rm = sub.add_parser("remove", help="ユーザーを消す(そのキーはすぐ使えなくなる)")
    p_rm.add_argument("--name", required=True)
    args = parser.parse_args(argv)

    if args.cmd == "add":
        try:
            key = add_user(args.name, args.role, email=args.email, with_key=not args.no_key)
        except ValueError as exc:
            print(f"エラー: {exc}", file=sys.stderr)
            return 1
        print(f"作成しました: {args.name} ({args.role})")
        if key:
            print(f"API キー(この 1 回しか表示されません): {key}")
        return 0
    if args.cmd == "list":
        for u in load_users():
            print(f"{u['name']:<20} {u['role']:<9} キー={'あり' if u.get('key_sha256') else 'なし'}  {u.get('email', '')}")
        return 0
    if args.cmd == "remove":
        if remove_user(args.name):
            print(f"消しました: {args.name}")
            return 0
        print(f"見つかりません: {args.name}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
