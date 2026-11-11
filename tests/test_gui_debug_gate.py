"""監視 HUD(/hud.html・/hud.js・/ws/hud)は開発者・デバッグ向け。本番では既定で無効、有効にしても admin だけ(QL6)。

- off(ローカルの単独利用): 従来どおり使える
- required(本番): VLM_DEBUG が無ければ、誰でも 404(ページ)/ 1008(WebSocket)= 存在しない扱い
- required + VLM_DEBUG=1: viewer / operator は 401・403 / 1008、admin は通る
- 通常の操作パネルが使う /api/observation・/api/status などは、影響を受けない
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.websockets import WebSocketDisconnect

from vlm_auto_replay.gui import audit, auth

LOCAL = {"Host": "127.0.0.1:8765"}
HUD_PAGES = ("/hud.html", "/hud.js")


@pytest.fixture()
def users_file(tmp_path: Path, monkeypatch) -> Path:
    f = tmp_path / "auth_users.json"
    monkeypatch.setenv("VLM_AUTH_FILE", str(f))
    monkeypatch.delenv("VLM_AUTH", raising=False)
    monkeypatch.delenv(auth.DEBUG_ENV, raising=False)
    return f


@pytest.fixture()
def keys(users_file, monkeypatch):
    made = {role: auth.add_user(role, role) for role in auth.ROLES}
    monkeypatch.setenv("VLM_AUTH", "required")
    return made


def h(key: str | None = None) -> dict:
    return {**LOCAL, **({"X-API-Key": key} if key else {})}


def ws_accepted(client, headers) -> bool:
    try:
        with client.websocket_connect("/ws/hud", headers=headers):
            return True
    except WebSocketDisconnect:
        return False


def entries() -> list[dict]:
    p = audit.audit_path()
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


# ---- off(ローカルの単独利用)
def test_off_keeps_the_hud_available(users_file, client):
    assert client.get("/hud.html", headers=LOCAL).status_code == 200
    assert "Monitoring HUD" in client.get("/hud.html", headers=LOCAL).text
    assert client.get("/hud.js", headers=LOCAL).status_code == 200
    assert ws_accepted(client, LOCAL)
    assert client.get("/api/auth/me", headers=LOCAL).json()["debug"] is True


# ---- required + VLM_DEBUG 未設定 = 既定で無効
def test_disabled_by_default_in_required_mode_for_everyone(keys, client):
    for page in HUD_PAGES:
        assert client.get(page, headers=h()).status_code == 404, page  # 未認証でも 404(存在しない扱い)
        for role in auth.ROLES:
            assert client.get(page, headers=h(keys[role])).status_code == 404, (page, role)
    for role in auth.ROLES:
        assert ws_accepted(client, h(keys[role])) is False, role


def test_disabled_websocket_attempts_are_audited_with_the_reason(keys, client):
    ws_accepted(client, h(keys["admin"]))
    assert [(e["event"], e["path"], e["status"], e["detail"]) for e in entries()] == [
        ("denied", "/ws/hud", 1008, "debug-disabled")
    ]


def test_gui_is_told_debug_is_unavailable_to_everyone(keys, client):
    for role in auth.ROLES:
        assert client.get("/api/auth/me", headers=h(keys[role])).json()["debug"] is False
    assert client.get("/api/auth/me", headers=h()).json()["debug"] is False


def test_the_normal_panel_is_not_affected(keys, client):
    assert client.get("/api/status", headers=h(keys["viewer"])).status_code == 200
    assert client.get("/", headers=h()).status_code == 200  # 操作パネル本体(静的)
    # 操作パネルのステップ履歴が使う観測の画像は、利用者向けのまま(権限は viewer)
    assert client.get("/api/observation/none", headers=h(keys["viewer"])).status_code == 404  # 中身が無い、の 404


# ---- required + VLM_DEBUG=1 = 有効にしても admin だけ
@pytest.fixture()
def debug_on(keys, monkeypatch):
    monkeypatch.setenv(auth.DEBUG_ENV, "1")
    return keys


def test_enabled_hud_pages_are_admin_only(debug_on, client):
    for page in HUD_PAGES:
        assert client.get(page, headers=h()).status_code == 401, page
        assert client.get(page, headers=h(debug_on["viewer"])).status_code == 403, page
        assert client.get(page, headers=h(debug_on["operator"])).status_code == 403, page
        assert client.get(page, headers=h(debug_on["admin"])).status_code == 200, page


def test_enabled_hud_websocket_is_admin_only(debug_on, client):
    assert ws_accepted(client, h()) is False
    assert ws_accepted(client, h(debug_on["viewer"])) is False
    assert ws_accepted(client, h(debug_on["operator"])) is False
    assert ws_accepted(client, h(debug_on["admin"])) is True


def test_gui_is_told_debug_is_available_only_to_admin_when_enabled(debug_on, client):
    got = {role: client.get("/api/auth/me", headers=h(debug_on[role])).json()["debug"] for role in auth.ROLES}
    assert got == {"viewer": False, "operator": False, "admin": True}


def test_denied_hud_page_attempts_are_audited(debug_on, client):
    client.get("/hud.html", headers=h(debug_on["operator"]))
    assert any(
        e["event"] == "denied" and e["actor"] == "operator" and e["path"] == "/hud.html" and e["status"] == 403
        for e in entries()
    )


# ---- 環境変数の読み方・GUI の出し分け
@pytest.mark.parametrize(
    "value,expected",
    [("1", True), ("true", True), ("YES", True), (" on ", True), ("0", False), ("", False), ("no", False)],
)
def test_debug_env_parsing(users_file, monkeypatch, value, expected):
    monkeypatch.setenv(auth.DEBUG_ENV, value)
    assert auth.debug_enabled() is expected


def test_gui_script_hides_the_hud_link_for_people_who_cannot_use_it(client):
    script = client.get("/auth.js", headers=LOCAL).text
    assert "me.debug !== true" in script and 'a[href="/hud.html"]' in script
