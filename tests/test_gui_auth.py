"""gui/auth.py: API キー+役割、Cloudflare Access、fail-closed、ログイン(クッキー)、/ws/hud の認証、CLI。"""
from __future__ import annotations

import time
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.websockets import WebSocketDisconnect

from vlm_auto_replay.gui import auth

LOCAL = {"Host": "127.0.0.1:8765"}


@pytest.fixture()
def users_file(tmp_path: Path, monkeypatch) -> Path:
    f = tmp_path / "auth_users.json"
    monkeypatch.setenv("VLM_AUTH_FILE", str(f))
    for name in ("VLM_AUTH", auth.TEAM_DOMAIN_ENV, auth.AUD_ENV):
        monkeypatch.delenv(name, raising=False)
    return f


@pytest.fixture()
def keys(users_file, monkeypatch):
    """viewer / operator / admin を作り、required にする。client より先に評価される必要がある。"""
    made = {role: auth.add_user(role, role) for role in auth.ROLES}
    monkeypatch.setenv("VLM_AUTH", "required")
    return made


def h(key: str) -> dict:
    return {**LOCAL, "X-API-Key": key}


# ── off(既定)と fail-closed ────────────────────────────────────────────────────
def test_off_is_the_default_and_needs_no_key(users_file, client):
    assert client.get("/api/status", headers=LOCAL).status_code == 200
    me = client.get("/api/auth/me", headers=LOCAL).json()
    assert me["mode"] == "off" and me["role"] == "admin"


def test_required_without_users_refuses_to_start(users_file, monkeypatch):
    monkeypatch.setenv("VLM_AUTH", "required")
    with pytest.raises(RuntimeError, match="ユーザーが 1 人もいません"):
        auth.ensure_auth_configured()


def test_invalid_mode_is_an_error(users_file, monkeypatch):
    monkeypatch.setenv("VLM_AUTH", "yes")
    with pytest.raises(RuntimeError):
        auth.auth_mode()


# ── 役割 ──────────────────────────────────────────────────────────────────────
def test_login_state_is_open_but_data_is_not(keys, client):
    assert client.get("/api/auth/me", headers=LOCAL).json()["authenticated"] is False
    assert client.get("/api/status", headers=LOCAL).status_code == 401
    assert client.get("/api/history", headers=h("vlm_wrong")).status_code == 401


def test_viewer_reads_but_cannot_start_a_run(keys, client):
    assert client.get("/api/status", headers=h(keys["viewer"])).status_code == 200
    assert client.get("/api/history", headers=h(keys["viewer"])).status_code == 200
    assert client.post("/api/run/start", json={}, headers=h(keys["viewer"])).status_code == 403
    assert client.post("/api/run/stop", headers=h(keys["viewer"])).status_code == 403


def test_operator_can_run_but_not_change_skills_or_hid_backend(keys, client):
    k = keys["operator"]
    assert client.post("/api/run/start", json={}, headers=h(k)).status_code == 422  # 認証は通り、中身の検証へ
    assert client.post("/api/run/stop", headers=h(k)).status_code == 200
    assert client.post("/api/todo/decompose", json={}, headers=h(k)).status_code == 422
    assert client.post("/api/skills", json={}, headers=h(k)).status_code == 403
    assert client.delete("/api/skills/x", headers=h(k)).status_code == 403
    assert client.post("/api/settings/hid-backend", json={}, headers=h(k)).status_code == 403


def test_admin_can_change_skills_and_hid_backend(keys, client):
    k = keys["admin"]
    assert client.post("/api/skills", json={}, headers=h(k)).status_code == 422
    assert client.delete("/api/skills/none", headers=h(k)).status_code == 404
    assert client.post("/api/settings/hid-backend", json={}, headers=h(k)).status_code == 422


def test_observation_svg_requires_auth(keys, client):
    assert client.get("/api/observation/x", headers=LOCAL).status_code == 401
    assert client.get("/api/observation/x", headers=h(keys["viewer"])).status_code == 404  # 認証は通る


def test_bearer_header_is_accepted(keys, client):
    r = client.get("/api/status", headers={**LOCAL, "Authorization": f"Bearer {keys['viewer']}"})
    assert r.status_code == 200


# ── ログイン(クッキー)と WebSocket ──────────────────────────────────────────────
def test_login_cookie_works_for_http_and_websocket(keys, client, monkeypatch):
    # 監視 HUD(/ws/hud)は開発者・デバッグ向け: 本番では VLM_DEBUG=1 で有効にして、admin だけ(test_gui_debug_gate.py)。
    # ここは、クッキーが WebSocket のハンドシェイクにも付くこと(仕組み)を確かめる
    monkeypatch.setenv(auth.DEBUG_ENV, "1")
    r = client.post("/api/auth/login", json={"api_key": keys["admin"]}, headers=LOCAL)
    assert r.status_code == 200 and r.json()["role"] == "admin"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert client.get("/api/status", headers=LOCAL).status_code == 200  # ヘッダー無し(クッキーだけ)
    with client.websocket_connect("/ws/hud", headers=LOCAL):  # 同じクッキーがハンドシェイクに付く
        pass
    client.post("/api/auth/logout", headers=LOCAL)
    assert client.get("/api/status", headers=LOCAL).status_code == 401


def test_websocket_without_credentials_is_refused(keys, client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/hud", headers=LOCAL):
            pass


def test_websocket_with_api_key_header_is_accepted(keys, client, monkeypatch):
    monkeypatch.setenv(auth.DEBUG_ENV, "1")  # HUD は本番では既定で無効・有効でも admin だけ
    with client.websocket_connect("/ws/hud", headers=h(keys["admin"])):
        pass
    with pytest.raises(WebSocketDisconnect):  # viewer は、有効でも入れない
        with client.websocket_connect("/ws/hud", headers=h(keys["viewer"])):
            pass


def test_login_with_wrong_key_is_401(keys, client):
    assert client.post("/api/auth/login", json={"api_key": "nope"}, headers=LOCAL).status_code == 401


def test_login_from_another_site_is_refused_by_origin_guard(keys, client):
    r = client.post("/api/auth/login", json={"api_key": keys["viewer"]},
                    headers={**LOCAL, "Origin": "http://evil.example"})
    assert r.status_code == 403


def test_removed_user_loses_access_immediately(keys, client):
    assert client.get("/api/status", headers=h(keys["viewer"])).status_code == 200
    assert auth.remove_user("viewer")
    assert client.get("/api/status", headers=h(keys["viewer"])).status_code == 401


def test_only_the_hash_is_stored(keys, users_file):
    text = users_file.read_text(encoding="utf-8")
    assert all(k not in text for k in keys.values())
    assert auth.hash_key(keys["admin"]) in text


# ── Cloudflare Access ─────────────────────────────────────────────────────────
TEAM = "myteam.cloudflareaccess.com"
AUD = "aud-123"


@pytest.fixture()
def access(users_file, monkeypatch):
    auth.add_user("carol", "operator", email="carol@example.com", with_key=False)
    auth.add_user("root", "admin")
    monkeypatch.setenv("VLM_AUTH", "required")
    monkeypatch.setenv(auth.TEAM_DOMAIN_ENV, f"https://{TEAM}/")
    monkeypatch.setenv(auth.AUD_ENV, AUD)
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(auth, "_signing_key", lambda token: private.public_key())

    def token(**over):
        now = int(time.time())
        claims = {"email": "carol@example.com", "aud": AUD, "iss": f"https://{TEAM}", "iat": now, "exp": now + 300}
        claims.update(over)
        return jwt.encode(claims, private, algorithm="RS256")

    return token


def acc(token: str) -> dict:
    return {**LOCAL, "Cf-Access-Jwt-Assertion": token}


def test_access_maps_email_to_role(access, client):
    assert client.get("/api/status", headers=acc(access())).status_code == 200
    assert client.post("/api/run/start", json={}, headers=acc(access())).status_code == 422  # operator
    assert client.post("/api/skills", json={}, headers=acc(access())).status_code == 403


def test_access_unregistered_email_is_403(access, client):
    assert client.get("/api/status", headers=acc(access(email="stranger@example.com"))).status_code == 403


def test_access_rejects_wrong_audience_issuer_expired(access, client):
    assert client.get("/api/status", headers=acc(access(aud="other"))).status_code == 401
    assert client.get("/api/status", headers=acc(access(iss="https://evil.example"))).status_code == 401
    assert client.get("/api/status", headers=acc(access(exp=int(time.time()) - 10))).status_code == 401


def test_access_rejects_hs256_and_unsigned(access, client):
    now = int(time.time())
    claims = {"email": "carol@example.com", "aud": AUD, "iss": f"https://{TEAM}", "iat": now, "exp": now + 300}
    assert client.get("/api/status", headers=acc(jwt.encode(claims, "secret" * 8, algorithm="HS256"))).status_code == 401
    assert client.get("/api/status", headers=acc(jwt.encode(claims, key=None, algorithm="none"))).status_code == 401


def test_access_header_is_ignored_when_not_configured(keys, client):
    assert client.get("/api/status", headers=acc("anything")).status_code == 401


# ── CLI ───────────────────────────────────────────────────────────────────────
def test_cli_add_list_remove(users_file, capsys):
    assert auth._cli(["add", "--name", "dave", "--role", "viewer"]) == 0
    assert "vlm_" in capsys.readouterr().out
    assert auth._cli(["list"]) == 0
    listed = capsys.readouterr().out
    assert "dave" in listed and "vlm_" not in listed
    assert auth._cli(["add", "--name", "dave", "--role", "admin"]) == 1
    assert auth._cli(["remove", "--name", "dave"]) == 0
    assert auth._cli(["remove", "--name", "dave"]) == 1
