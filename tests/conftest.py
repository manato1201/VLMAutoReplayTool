from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from vlm_auto_replay.prompts.model_client import (
    ScriptedFoundationModelClient,
    configure_model_client,
    reset_model_client,
)


@pytest.fixture
def scripted_client() -> ScriptedFoundationModelClient:
    client = ScriptedFoundationModelClient()
    configure_model_client(client)
    yield client
    reset_model_client()


@pytest.fixture
def client(monkeypatch):
    """GUI(FastAPI)向けの共有フィクスチャ。test_gui_server.py / test_gui_hud.py の両方が使う。

    DemoModelClient/DemoGameが既定で動くため、実VLM/実HIDなしでエンドツーエンドに近い
    形で検証できる。モデルクライアントのグローバル状態は他テストと共有されるため、
    テストの前後で明示的にリセットしてから TestClient のlifespan(起動時のみ
    DemoModelClientを注入する)に解決させる。

    RuntimeStateはSQLiteに永続化するため、テストがユーザーの実DB
    (~/.vlm_auto_replay/gui.sqlite3)に触れないよう、テストごとに`:memory:`DBを
    持つ新しいRuntimeStateへ差し替える(サーバ本体のモジュールグローバルをmonkeypatch)。
    """
    reset_model_client()
    from fastapi.testclient import TestClient

    import vlm_auto_replay.gui.server as server_module
    from vlm_auto_replay.gui.runtime import RuntimeState

    monkeypatch.setattr(server_module, "state", RuntimeState(db_path=":memory:"))

    with TestClient(server_module.app) as c:
        yield c
    reset_model_client()


@pytest.fixture(autouse=True)
def _allow_testserver_host(monkeypatch):
    """TestClient は Host: testserver で送る。Host の許可リスト(gui/security.py)に足しておく。"""
    monkeypatch.setenv("VLM_ALLOWED_HOSTS", "testserver")


@pytest.fixture(autouse=True)
def _isolate_audit_log(tmp_path, monkeypatch):
    """監査ログ(gui/audit.py)を、テストごとの一時ファイルに向ける(本物の ~/.vlm_auto_replay/audit.jsonl に書かない)。"""
    monkeypatch.setenv("VLM_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
