"""gui/security.py: Host の許可リスト(DNS rebinding)・変更操作の Origin 検査(CSRF)・/ws/hud の Origin 検査。"""
from __future__ import annotations

import pytest
from starlette.websockets import WebSocketDisconnect

from vlm_auto_replay.gui.security import ALLOWED_HOSTS_ENV, host_only, is_same_origin, resolve_allowed_hosts

LOCAL = {"Host": "127.0.0.1:8765"}


def test_host_only():
    assert host_only("127.0.0.1:8765") == "127.0.0.1"
    assert host_only("[::1]:8765") == "::1"


def test_unknown_host_is_refused(client):
    r = client.get("/api/status", headers={"Host": "evil.example"})
    assert r.status_code == 403
    assert ALLOWED_HOSTS_ENV in r.json()["detail"]


def test_loopback_host_is_allowed(client):
    assert client.get("/api/status", headers=LOCAL).status_code == 200


def test_cross_site_run_start_is_refused_before_anything_runs(client):
    r = client.post("/api/run/start", json={"todoId": "x"}, headers={**LOCAL, "Origin": "https://evil.example"})
    assert r.status_code == 403
    assert "Origin" in r.json()["detail"]


def test_null_origin_is_refused(client):
    assert client.post("/api/run/stop", headers={**LOCAL, "Origin": "null"}).status_code == 403


def test_same_origin_post_passes_the_guard(client):
    assert client.post("/api/run/stop", headers={**LOCAL, "Origin": "http://127.0.0.1:8765"}).status_code == 200


def test_post_without_origin_passes_the_guard(client):
    assert client.post("/api/run/stop", headers=LOCAL).status_code == 200


def test_ws_hud_refuses_a_foreign_origin(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/hud", headers={**LOCAL, "Origin": "https://evil.example"}):
            pass


def test_ws_hud_refuses_an_unknown_host(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/hud", headers={"Host": "evil.example"}):
            pass


def test_ws_hud_allows_same_origin(client):
    with client.websocket_connect("/ws/hud", headers={**LOCAL, "Origin": "http://127.0.0.1:8765"}) as ws:
        ws.close()


def test_env_can_add_a_host(monkeypatch):
    monkeypatch.setenv(ALLOWED_HOSTS_ENV, "testserver, vlm.lan")
    assert "vlm.lan" in resolve_allowed_hosts()


def test_is_same_origin():
    assert is_same_origin("http://127.0.0.1:8765", "127.0.0.1:8765")
    assert not is_same_origin("http://127.0.0.1:5173", "127.0.0.1:8765")
