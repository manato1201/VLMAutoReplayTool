"""管理画面の分離(QL6): スキルの追加・削除と HID の設定は、別のページ・別のスクリプト(/admin.html・/admin.js)にあり、
サーバーが admin に限る。操作パネル(/・/app.js)には、管理操作のコードを載せない。

- off(ローカルの単独利用): 従来どおり使える(全員 admin 扱い)
- required: 未認証は 401・viewer / operator は 403・admin だけ 200(VLM_DEBUG とは無関係)
- 操作パネル側は、読み取りだけ(管理操作の部品も、API の呼び出しも持たない)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vlm_auto_replay.gui import audit, auth

LOCAL = {"Host": "127.0.0.1:8765"}
ADMIN_PAGES = ("/admin.html", "/admin.js")
# 管理操作の目印(これらは admin のページにだけある)
ADMIN_MARKERS = ("add-skill-btn", "api/skills", "hid-backend", "deleteJson", "/api/settings")


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


def text(client, path, headers=LOCAL) -> str:
    r = client.get(path, headers=headers)
    assert r.status_code == 200, (path, r.status_code)
    return r.text


def entries() -> list[dict]:
    p = audit.audit_path()
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


# ---- off
def test_off_keeps_the_admin_page_available(users_file, client):
    assert "ADMIN" in text(client, "/admin.html")
    assert "add-skill-btn" in text(client, "/admin.js") or "api/skills" in text(client, "/admin.js")


# ---- required: 役割で守る
def test_admin_pages_are_admin_only(keys, client):
    for page in ADMIN_PAGES:
        assert client.get(page, headers=h()).status_code == 401, page
        assert client.get(page, headers=h(keys["viewer"])).status_code == 403, page
        assert client.get(page, headers=h(keys["operator"])).status_code == 403, page
        assert client.get(page, headers=h(keys["admin"])).status_code == 200, page


def test_admin_pages_do_not_depend_on_the_debug_flag(keys, client, monkeypatch):
    monkeypatch.setenv(auth.DEBUG_ENV, "1")
    assert client.get("/admin.html", headers=h(keys["operator"])).status_code == 403
    monkeypatch.delenv(auth.DEBUG_ENV)
    assert client.get("/admin.html", headers=h(keys["admin"])).status_code == 200


def test_denied_admin_page_attempts_are_audited(keys, client):
    client.get("/admin.html", headers=h(keys["viewer"]))
    client.get("/admin.js", headers=h())
    got = [(e["event"], e["actor"], e["path"], e["status"]) for e in entries()]
    assert ("denied", "viewer", "/admin.html", 403) in got
    assert ("denied", "anonymous", "/admin.js", 401) in got


def test_the_admin_api_stays_admin_only(keys, client):
    # ページだけでなく、API 側も(分離しても、サーバーの判断は同じ)
    assert client.post("/api/skills", json={}, headers=h(keys["operator"])).status_code == 403
    assert client.delete("/api/skills/x", headers=h(keys["operator"])).status_code == 403
    assert client.post("/api/settings/hid-backend", json={}, headers=h(keys["operator"])).status_code == 403


# ---- 操作パネルは読み取りだけ(管理操作のコードを載せない)
def test_the_control_panel_carries_no_admin_code(users_file, client):
    for page in ("/index.html", "/app.js"):
        body = text(client, page)
        for marker in ADMIN_MARKERS:
            assert marker not in body, f"{page} に管理操作の目印 {marker!r} が残っている"


def test_the_admin_page_carries_the_admin_code(users_file, client):
    body = text(client, "/admin.js") + text(client, "/admin.html")
    for marker in ("add-skill-btn", "api/skills", "hid-backend", "deleteJson", "/api/settings"):
        assert marker in body, marker


def test_the_control_panel_links_to_the_admin_page_and_hides_it_from_others(users_file, client):
    assert 'href="/admin.html"' in text(client, "/index.html")
    script = text(client, "/auth.js")
    assert 'me.role !== "admin"' in script and 'a[href="/admin.html"]' in script
    # 操作パネルのボタンの無効化(operator)には、もう管理操作の項目が無い
    assert "skill-game-title" not in script and "hid-backend-options" not in script


def test_the_control_panel_still_lists_skills_read_only(users_file, client):
    body = text(client, "/app.js")
    assert "function renderSkills" in body and "btn-delete" not in body
