"""GUI: FastAPIサーバのエンドポイント検証。

DemoModelClient/DemoGameが既定で動くため、実VLM/実HIDなしでエンドツーエンドに近い
形で検証できる。モデルクライアントのグローバル状態は他テストと共有されるため、
各テストの前後で明示的にリセットしてから TestClient のlifespan(起動時のみ
DemoModelClientを注入する)に解決させる。

RuntimeStateはSQLiteに永続化するため、テストがユーザーの実DB
(~/.vlm_auto_replay/gui.sqlite3)に触れないよう、テストごとに`:memory:`DBを
持つ新しいRuntimeStateへ差し替える(サーバ本体のモジュールグローバルをmonkeypatch)。
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from vlm_auto_replay.prompts.model_client import reset_model_client


@pytest.fixture
def client(monkeypatch):
    reset_model_client()
    import vlm_auto_replay.gui.server as server_module
    from vlm_auto_replay.gui.runtime import RuntimeState

    monkeypatch.setattr(server_module, "state", RuntimeState(db_path=":memory:"))

    with TestClient(server_module.app) as c:
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
    assert status["stepsToWin"] == 5
    assert status["progress"] == status["stepsToWin"]  # 完走後は進捗が最大値に達している

    # StepLogのobservationRefが実際に画像(SVG)として取得できること。
    obs_resp = client.get(f"/api/observation/{status['logs'][0]['observationRef']}")
    assert obs_resp.status_code == 200
    assert obs_resp.headers["content-type"].startswith("image/svg+xml")
    assert b"<svg" in obs_resp.content


def test_observation_endpoint_returns_404_for_unknown_ref(client):
    resp = client.get("/api/observation/does-not-exist")
    assert resp.status_code == 404


def test_add_and_delete_skill(client):
    resp = client.post("/api/skills", json={"gameTitle": "MyGame", "proceduralText": "1. attack\n2. heal"})
    assert resp.status_code == 200
    skill = resp.json()["skill"]
    assert skill["gameTitle"] == "MyGame"
    assert skill["type"] == "procedure"
    assert skill["createdBy"] == "manual"

    status = client.get("/api/status").json()
    skill_ids = [s["skillId"] for s in status["skills"]]
    assert skill["skillId"] in skill_ids
    assert len(status["skills"]) == 2  # 既定のdemo-skill-1 + 追加した1件

    resp = client.delete(f"/api/skills/{skill['skillId']}")
    assert resp.status_code == 200
    status = client.get("/api/status").json()
    assert skill["skillId"] not in [s["skillId"] for s in status["skills"]]


def test_add_skill_rejects_empty_procedural_text(client):
    resp = client.post("/api/skills", json={"gameTitle": "MyGame", "proceduralText": "   "})
    assert resp.status_code == 400


def test_delete_unknown_skill_returns_404(client):
    resp = client.delete("/api/skills/does-not-exist")
    assert resp.status_code == 404


def test_history_lists_completed_run_and_detail_matches_status_logs(client):
    resp = client.post("/api/todo/decompose", json={"goal": "履歴テスト"})
    todo = resp.json()["todos"][0]
    client.post("/api/run/start", json={"todoId": todo["todoId"], "maxSteps": 12})

    deadline = time.monotonic() + 10
    status = client.get("/api/status").json()
    while status["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.2)
        status = client.get("/api/status").json()
    assert status["status"] == "done"

    history = client.get("/api/history").json()["runs"]
    assert len(history) == 1
    assert history[0]["todoId"] == todo["todoId"]
    assert history[0]["todoDescription"] == todo["description"]
    assert history[0]["status"] == "done"
    assert history[0]["finishedAt"] is not None

    detail = client.get(f"/api/history/{history[0]['runId']}").json()
    assert detail["run"]["runId"] == history[0]["runId"]
    assert [log["stepIndex"] for log in detail["logs"]] == [log["stepIndex"] for log in status["logs"]]


def test_history_detail_returns_404_for_unknown_run_id(client):
    resp = client.get("/api/history/does-not-exist")
    assert resp.status_code == 404


def test_skill_library_survives_runtime_state_restart(tmp_path):
    """RuntimeStateを同じdb_pathで再構築しても、追加したスキルが失われないこと
    (サーバー再起動をまたいだ永続化の実証)。
    """
    from vlm_auto_replay.gui.runtime import RuntimeState

    db_path = tmp_path / "gui.sqlite3"
    first = RuntimeState(db_path=db_path)
    added = first.add_skill("PersistedGame", "1. explore\n2. fight")

    second = RuntimeState(db_path=db_path)
    assert added.skillId in second.skill_library
    assert second.skill_library[added.skillId].gameTitle == "PersistedGame"

    second.remove_skill(added.skillId)
    third = RuntimeState(db_path=db_path)
    assert added.skillId not in third.skill_library
