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


def test_stall_demo_triggers_watchdog_intervention_and_rebuilds_todo(client):
    """stallDemo=Trueは意図的に完了しないゲームを使い、Watchdogの閾値到達による
    自動介入とTODO再構築(rebuild_todo)を確実に発生させる。
    """
    resp = client.post("/api/todo/decompose", json={"goal": "介入テスト"})
    todos = resp.json()["todos"]
    stalled_todo = todos[0]

    resp = client.post("/api/run/start", json={"todoId": stalled_todo["todoId"], "maxSteps": 20, "stallDemo": True})
    assert resp.status_code == 200

    deadline = time.monotonic() + 15
    status = client.get("/api/status").json()
    while status["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.2)
        status = client.get("/api/status").json()

    assert status["status"] == "intervened"
    assert len(status["logs"]) == status["watchdogThreshold"]  # 閾値到達で即座に介入する

    intervention = status["lastIntervention"]
    assert intervention is not None
    assert intervention["todoId"] == stalled_todo["todoId"]
    assert intervention["stepCount"] == status["watchdogThreshold"]
    assert len(intervention["newTodoIds"]) > 0

    # 停滞したTODOは新しいTODO群に差し替えられ、他の未完了TODO(2件)は残る。
    remaining_ids = [t["todoId"] for t in status["todos"]]
    assert stalled_todo["todoId"] not in remaining_ids
    assert all(new_id in remaining_ids for new_id in intervention["newTodoIds"])
    assert todos[1]["todoId"] in remaining_ids
    assert todos[2]["todoId"] in remaining_ids

    # 実行履歴側でもstatus="intervened"として記録されていること。
    history = client.get("/api/history").json()["runs"]
    assert history[0]["status"] == "intervened"


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


def test_settings_default_hid_backend_is_none(client):
    settings = client.get("/api/settings").json()["hidBackend"]
    assert settings["current"] == "none"
    assert settings["error"] is None
    names = [opt["name"] for opt in settings["options"]]
    assert names == ["none", "sendinput", "vigem"]


def test_settings_switch_to_sendinput_succeeds_on_windows(client):
    """SendInputBackendはctypes(標準ライブラリ)のみに依存するため、Windows上では
    実際にドライバ等なしで構築できる(呼び出しはしない、構築のみ検証)。
    """
    resp = client.post("/api/settings/hid-backend", json={"name": "sendinput"})
    assert resp.status_code == 200
    assert resp.json()["hidBackend"]["current"] == "sendinput"
    assert resp.json()["hidBackend"]["error"] is None

    status = client.get("/api/settings").json()["hidBackend"]
    assert status["current"] == "sendinput"


def test_settings_switch_to_vigem_without_vgamepad_returns_422_and_keeps_previous(client):
    """vgamepadパッケージが未インストールな環境(このテスト環境)では、ViGEmへの切り替えは
    明確なエラーになり、現在のバックエンドは変更されないままであること。
    """
    try:
        import vgamepad  # noqa: F401
    except ImportError:
        pass
    else:
        pytest.skip("vgamepadがインストールされている環境では別の検証が必要")

    resp = client.post("/api/settings/hid-backend", json={"name": "vigem"})
    assert resp.status_code == 422
    assert "vgamepad" in resp.json()["detail"]

    status = client.get("/api/settings").json()["hidBackend"]
    assert status["current"] == "none"  # 失敗時は変更されない
    assert status["error"] is not None


def test_settings_switch_to_unknown_backend_returns_400(client):
    resp = client.post("/api/settings/hid-backend", json={"name": "does-not-exist"})
    assert resp.status_code == 400
    # 失敗時は元の設定(none)のまま変わらない。
    assert client.get("/api/settings").json()["hidBackend"]["current"] == "none"
