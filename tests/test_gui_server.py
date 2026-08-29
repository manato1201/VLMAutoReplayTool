"""GUI: FastAPIサーバのエンドポイント検証。

DemoModelClient/DemoGameが既定で動くため、実VLM/実HIDなしでエンドツーエンドに近い
形で検証できる。モデルクライアントのグローバル状態は他テストと共有されるため、
各テストの前後で明示的にリセットしてから TestClient のlifespan(起動時のみ
DemoModelClientを注入する)に解決させる。
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from vlm_auto_replay.prompts.model_client import reset_model_client


@pytest.fixture
def client():
    reset_model_client()
    from vlm_auto_replay.gui.server import app, state

    # 前のテストの実行結果が残らないよう、テストごとに状態を初期化する。
    state.todos = []
    state.logs = []
    state.status = "idle"
    state.active_todo_id = None
    state.error = None

    with TestClient(app) as c:
        yield c
    reset_model_client()


def test_decompose_rejects_empty_goal(client):
    resp = client.post("/api/todo/decompose", json={"goal": "   "})
    assert resp.status_code == 400


def test_start_run_rejects_max_steps_out_of_range(client):
    resp = client.post("/api/todo/decompose", json={"goal": "test goal"})
    todo_id = resp.json()["todos"][0]["todoId"]
    resp = client.post("/api/run/start", json={"todoId": todo_id, "maxSteps": 0})
    assert resp.status_code == 422
    resp = client.post("/api/run/start", json={"todoId": todo_id, "maxSteps": 10_000})
    assert resp.status_code == 422


def test_start_run_rejects_unknown_todo_id(client):
    client.post("/api/todo/decompose", json={"goal": "test goal"})
    resp = client.post("/api/run/start", json={"todoId": "does-not-exist"})
    assert resp.status_code == 400


def test_full_demo_run_reaches_done_with_live_logs(client):
    resp = client.post("/api/todo/decompose", json={"goal": "デモゴール"})
    assert resp.status_code == 200
    todos = resp.json()["todos"]
    assert len(todos) == 3

    resp = client.post("/api/run/start", json={"todoId": todos[0]["todoId"], "maxSteps": 12})
    assert resp.status_code == 200

    deadline = time.monotonic() + 10
    status = client.get("/api/status").json()
    while status["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.2)
        status = client.get("/api/status").json()

    assert status["status"] == "done"
    assert status["error"] is None
    assert len(status["logs"]) > 0
    assert status["logs"][0]["reasoning"]
    assert status["watchdogThreshold"] == 8
    assert len(status["skills"]) == 1
