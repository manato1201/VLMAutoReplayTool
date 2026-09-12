"""GUI: Phase8モニタリングHUDのWebSocket配信検証(/ws/hud)。

共有する `client` フィクスチャ(TestClient + `:memory:` RuntimeState)は
`conftest.py` に定義されている。REST APIのバリデーション/CRUDは `test_gui_server.py` を参照。
"""

from __future__ import annotations

import time


def test_hud_websocket_receives_logs_reset_then_todos_event_after_decompose(client):
    """decompose()はサーバ側のself.logsもリセットするため、HUDのStepLog蓄積も
    (次のRunを待たずに)logs_resetでクリアされ、続けてtodosイベントが届くこと。
    """
    with client.websocket_connect("/ws/hud") as ws:
        resp = client.post("/api/todo/decompose", json={"goal": "HUDテスト"})
        todos = resp.json()["todos"]
        reset_event = ws.receive_json()
        todos_event = ws.receive_json()
        assert reset_event["type"] == "logs_reset"
        assert todos_event["type"] == "todos"
        assert [t["todoId"] for t in todos_event["todos"]] == [t["todoId"] for t in todos]
        assert todos_event["completedTodoIds"] == []


def test_hud_websocket_receives_run_started_before_step_logs(client):
    """新しいRun開始時にrun_startedが最初に届くこと(HUD側が前Runのドット/ログを
    クリアするトリガーになる)。stepIndexはRunごとに0から振り直されるため、これが
    無いと複数回実行した際にHUDのドットチャートが前Runのものと混在してしまう。
    """
    resp = client.post("/api/todo/decompose", json={"goal": "HUD step"})
    todo = resp.json()["todos"][0]
    with client.websocket_connect("/ws/hud") as ws:
        client.post("/api/run/start", json={"todoId": todo["todoId"], "maxSteps": 12})
        run_started = ws.receive_json()
        first = ws.receive_json()
        second = ws.receive_json()
        assert run_started["type"] == "run_started"
        assert run_started["todoId"] == todo["todoId"]
        assert first["type"] == "step_log"
        assert first["log"]["stepIndex"] == 0
        assert second["type"] == "step_log"
        assert second["log"]["stepIndex"] == 1


def test_two_consecutive_runs_each_start_with_their_own_run_started(client):
    """1つのHUDセッション内で2回実行しても、2回目のrun_startedがちゃんと届くこと
    (フロントエンドはこれを合図に前RunのStepLog蓄積をクリアする)。
    """
    resp = client.post("/api/todo/decompose", json={"goal": "HUD two runs"})
    todos = resp.json()["todos"]

    with client.websocket_connect("/ws/hud") as ws:
        client.post("/api/run/start", json={"todoId": todos[0]["todoId"], "maxSteps": 12})
        deadline = time.monotonic() + 10
        status = client.get("/api/status").json()
        while status["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.2)
            status = client.get("/api/status").json()
        assert status["status"] == "done"

        # 1回目のRunぶんの run_started + step_log*N + todos(完了) を読み飛ばす。
        first_run_started_seen = False
        for _ in range(30):
            event = ws.receive_json()
            if event["type"] == "run_started":
                first_run_started_seen = True
            if event["type"] == "experience":
                break
        assert first_run_started_seen

        client.post("/api/run/start", json={"todoId": todos[1]["todoId"], "maxSteps": 12})
        second_run_started = None
        for _ in range(10):
            event = ws.receive_json()
            if event["type"] == "run_started":
                second_run_started = event
                break
        assert second_run_started is not None
        assert second_run_started["todoId"] == todos[1]["todoId"]


def test_hud_websocket_receives_experience_toast_after_done_run(client):
    """完走(status=="done")後にextract_experience(Phase1)がHUD配信されること。"""
    resp = client.post("/api/todo/decompose", json={"goal": "HUD experience"})
    todo = resp.json()["todos"][0]
    with client.websocket_connect("/ws/hud") as ws:
        client.post("/api/run/start", json={"todoId": todo["todoId"], "maxSteps": 12})
        experience_event = None
        for _ in range(30):
            event = ws.receive_json()
            if event["type"] == "experience":
                experience_event = event
                break
        assert experience_event is not None
        assert experience_event["experience"]["title"]


def test_hud_websocket_receives_watchdog_intervened_event_and_todos_update(client):
    """stallDemoでWatchdogが実際に介入した際、watchdog_intervenedイベントと
    再構築後のtodosイベントの両方がHUDへ配信されること。stepIndexは0始まりなので
    閾値(既定8)到達時点のstepIndexは7になる。
    """
    resp = client.post("/api/todo/decompose", json={"goal": "HUD intervene"})
    todo = resp.json()["todos"][0]
    with client.websocket_connect("/ws/hud") as ws:
        client.post(
            "/api/run/start", json={"todoId": todo["todoId"], "maxSteps": 20, "stallDemo": True}
        )
        intervened_event = None
        todos_after_rebuild = None
        for _ in range(30):
            event = ws.receive_json()
            if event["type"] == "watchdog_intervened":
                intervened_event = event
            elif event["type"] == "todos" and intervened_event is not None:
                todos_after_rebuild = event
                break

        assert intervened_event is not None
        assert intervened_event["stepIndex"] == 7
        assert intervened_event["message"]
        assert intervened_event["intervention"]["todoId"] == todo["todoId"]
        # DemoModelClientのdiagnose_stallは常にshouldRebuildTodo=Falseを返すため、この介入は
        # 閾値到達(強制発動)経路によるもの。直近のdiagnose_stall結果(無関係・古い可能性がある)を
        # 理由として誤配信しないよう、diagnosisはNoneであること(回帰テスト)。
        assert intervened_event["diagnosis"] is None
        assert "8ステップ継続" in intervened_event["message"]

        assert todos_after_rebuild is not None
        remaining_ids = [t["todoId"] for t in todos_after_rebuild["todos"]]
        assert todo["todoId"] not in remaining_ids


def test_hud_websocket_receives_skills_event_on_add_and_remove(client):
    with client.websocket_connect("/ws/hud") as ws:
        resp = client.post("/api/skills", json={"gameTitle": "HUDGame", "proceduralText": "1. do it"})
        skill_id = resp.json()["skill"]["skillId"]

        added_event = ws.receive_json()
        assert added_event["type"] == "skills"
        assert any(s["skillId"] == skill_id for s in added_event["skills"])

        client.delete(f"/api/skills/{skill_id}")
        removed_event = ws.receive_json()
        assert removed_event["type"] == "skills"
        assert all(s["skillId"] != skill_id for s in removed_event["skills"])
