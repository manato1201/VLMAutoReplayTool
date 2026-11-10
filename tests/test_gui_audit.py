"""gui/audit.py: 監査ログ(誰が・いつ・何をしたか)。本文・キー・クッキーは記録しない。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.websockets import WebSocketDisconnect

from vlm_auto_replay.gui import audit, auth

LOCAL = {"Host": "127.0.0.1:8765"}


@pytest.fixture()
def users_file(tmp_path: Path, monkeypatch) -> Path:
    f = tmp_path / "auth_users.json"
    monkeypatch.setenv("VLM_AUTH_FILE", str(f))
    monkeypatch.delenv("VLM_AUTH", raising=False)
    return f


@pytest.fixture()
def keys(users_file, monkeypatch):
    made = {role: auth.add_user(role, role) for role in auth.ROLES}
    monkeypatch.setenv("VLM_AUTH", "required")
    return made


def h(key: str) -> dict:
    return {**LOCAL, "X-API-Key": key}


def entries() -> list[dict]:
    p = audit.audit_path()
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def test_a_write_is_recorded_with_who_and_what(keys, client):
    r = client.post("/api/run/stop", headers=h(keys["operator"]))
    assert r.status_code == 200
    e = entries()[-1]
    assert (e["event"], e["actor"], e["role"], e["source"], e["method"], e["path"], e["status"]) == (
        "http", "operator", "operator", "key", "POST", "/api/run/stop", 200)
    assert e["ts"].endswith("+00:00")


def test_reads_are_not_recorded(keys, client):
    client.get("/api/status", headers=h(keys["viewer"]))
    client.get("/api/history", headers=h(keys["viewer"]))
    client.get("/api/auth/me", headers=LOCAL)
    assert entries() == []


def test_denied_writes_record_the_actor(keys, client):
    assert client.post("/api/run/stop", headers=h(keys["viewer"])).status_code == 403
    assert client.post("/api/skills", json={}, headers=h(keys["operator"])).status_code == 403
    es = entries()
    assert [(e["event"], e["actor"], e["status"], e["path"]) for e in es] == [
        ("denied", "viewer", 403, "/api/run/stop"), ("denied", "operator", 403, "/api/skills")]


def test_unauthenticated_attempts_are_recorded_as_anonymous(keys, client):
    client.get("/api/status", headers=LOCAL)
    client.get("/api/status", headers=h("vlm_wrong"))
    assert [(e["actor"], e["status"]) for e in entries()] == [("anonymous", 401), ("anonymous", 401)]


def test_login_logout_recorded_once_with_the_name(keys, client):
    client.post("/api/auth/login", json={"api_key": "vlm_wrong"}, headers=LOCAL)
    client.post("/api/auth/login", json={"api_key": keys["viewer"]}, headers=LOCAL)
    client.post("/api/auth/logout", headers=LOCAL)
    assert [(e["event"], e["actor"], e["status"]) for e in entries()] == [
        ("login_failed", "anonymous", 401), ("login", "viewer", 200), ("logout", "viewer", 204)]


def test_websocket_refusals_are_recorded(keys, client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/hud", headers=LOCAL):
            pass
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/hud", headers={**LOCAL, "Origin": "https://evil.example"}):
            pass
    es = entries()
    assert [(e["event"], e["path"], e["status"], e.get("detail")) for e in es] == [
        ("denied", "/ws/hud", 1008, "auth"), ("denied", "/ws/hud", 1008, "host/origin")]


def test_nothing_secret_is_written(keys, client):
    client.post("/api/auth/login", json={"api_key": keys["admin"]}, headers=LOCAL)
    client.post("/api/skills", json={"gameTitle": "SECRET-BODY-TEXT", "proceduralText": "x"}, headers=h(keys["admin"]))
    client.get("/api/status?token=SECRET-QUERY", headers=h("vlm_secret_wrong_key_value"))
    text = audit.audit_path().read_text(encoding="utf-8")
    for secret in (*keys.values(), "SECRET-BODY-TEXT", "SECRET-QUERY", "vlm_secret_wrong_key_value"):
        assert secret not in text
    assert "?" not in "".join(json.loads(l)["path"] for l in text.splitlines())


def test_audit_endpoint_is_admin_only_and_newest_first(keys, client):
    client.post("/api/run/stop", headers=h(keys["operator"]))
    client.post("/api/run/stop", headers=h(keys["operator"]))
    assert client.get("/api/auth/audit", headers=h(keys["viewer"])).status_code == 403
    assert client.get("/api/auth/audit", headers=h(keys["operator"])).status_code == 403
    assert client.get("/api/auth/audit", headers=LOCAL).status_code == 401
    r = client.get("/api/auth/audit?limit=2", headers=h(keys["admin"]))
    assert r.status_code == 200
    es = r.json()["entries"]
    assert len(es) == 2 and es[0]["ts"] >= es[1]["ts"]


def test_write_failure_does_not_break_the_request(keys, client, monkeypatch):
    monkeypatch.setenv("VLM_AUDIT_LOG", str(audit.audit_path().parent))  # ディレクトリを指す = 書けない
    assert client.post("/api/run/stop", headers=h(keys["operator"])).status_code == 200


def test_rotation_and_broken_lines(users_file, monkeypatch, tmp_path):
    p = tmp_path / "rot.jsonl"
    monkeypatch.setenv("VLM_AUDIT_LOG", str(p))
    monkeypatch.setattr(audit, "MAX_BYTES", 200)
    for i in range(20):
        audit.record("http", actor=f"u{i}", method="POST", path="/api/x", status=200)
    assert p.exists() and p.with_suffix(".jsonl.1").exists()
    q = tmp_path / "broken.jsonl"
    monkeypatch.setenv("VLM_AUDIT_LOG", str(q))
    q.write_text('{"event":"a"}\nnot json\n{"event":"b"}\n', encoding="utf-8")
    assert [e["event"] for e in audit.read_recent(10)] == ["b", "a"]


def test_off_mode_records_writes_as_anonymous(users_file, client):
    client.post("/api/run/stop", headers=LOCAL)
    e = entries()[-1]
    assert (e["actor"], e["role"], e["source"]) == ("anonymous", "admin", "anonymous")
