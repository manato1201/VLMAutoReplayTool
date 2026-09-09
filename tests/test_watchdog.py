"""Phase5: Watchdogの二重条件・スコープ厳密化の検証。"""
from __future__ import annotations

from vlm_auto_replay.loop.schemas import StepLog
from vlm_auto_replay.loop.watchdog import Watchdog
from vlm_auto_replay.prompts.schemas import DecomposeGoalOutput, StallDiagnosis, TodoItem


def _make_log(step_index: int, todo_id: str) -> StepLog:
    return StepLog(
        stepIndex=step_index,
        timestamp="2026-08-12T00:00:00Z",
        todoId=todo_id,
        observationRef=f"obs:{step_index}",
        reasoning="r",
        actionTaken={"type": "api", "id": "advance", "params": {}},
        resultObservationSummary="s",
    )


def test_threshold_is_exposed_as_public_property():
    watchdog = Watchdog(stall_step_threshold=7)
    assert watchdog.threshold == 7


def test_temporary_failure_does_not_trigger_rebuild(scripted_client):
    """一時的失敗フィクスチャ(数ステップで自然回復)では再構築が発火しないこと。"""
    watchdog = Watchdog(stall_step_threshold=15)
    todo = TodoItem(todoId="t1", description="d", doneCriteria="c")
    logs = [_make_log(i, "t1") for i in range(3)]
    scripted_client.queue(
        "diagnose_stall", StallDiagnosis(rootCause="minor", nextApproach="retry", shouldRebuildTodo=False)
    )
    assert watchdog.should_intervene(todo, logs) is False


def test_threshold_forces_intervention_regardless_of_diagnosis(scripted_client):
    watchdog = Watchdog(stall_step_threshold=3)
    todo = TodoItem(todoId="t1", description="d", doneCriteria="c")
    logs = [_make_log(i, "t1") for i in range(3)]
    # diagnose_stallはFalseを返すが、閾値到達で強制発動するため呼び出しすら不要。
    scripted_client.queue(
        "diagnose_stall", StallDiagnosis(rootCause="x", nextApproach="y", shouldRebuildTodo=False)
    )
    assert watchdog.should_intervene(todo, logs) is True


def test_last_diagnosis_is_none_before_any_check():
    watchdog = Watchdog(stall_step_threshold=15)
    assert watchdog.last_diagnosis is None


def test_last_diagnosis_not_updated_when_threshold_short_circuits(scripted_client):
    """閾値到達(強制発動)経路ではdiagnose_stallを呼ばないため、last_diagnosisは
    直前の呼び出し結果のまま(=更新されない)ことを確認する。HUD配信側がこの
    「古い診断結果」を今回の介入理由として誤って使わないための前提となる仕様。
    """
    watchdog = Watchdog(stall_step_threshold=2)
    todo = TodoItem(todoId="t1", description="d", doneCriteria="c")

    # 1回目: 閾値未満でdiagnose_stallが呼ばれ、last_diagnosisが設定される。
    scripted_client.queue(
        "diagnose_stall", StallDiagnosis(rootCause="minor", nextApproach="retry", shouldRebuildTodo=False)
    )
    assert watchdog.should_intervene(todo, [_make_log(0, "t1")]) is False
    assert watchdog.last_diagnosis is not None
    assert watchdog.last_diagnosis.rootCause == "minor"

    # 2回目: 閾値到達で強制発動。diagnose_stallは呼ばれないため、last_diagnosisは
    # 1回目のまま(この呼び出しの「理由」ではないので、呼び出し側は使ってはいけない)。
    logs = [_make_log(0, "t1"), _make_log(1, "t1")]
    assert watchdog.should_intervene(todo, logs) is True
    assert watchdog.last_diagnosis.rootCause == "minor"  # 更新されていない(=古いまま)


def test_diagnose_stall_true_triggers_below_threshold(scripted_client):
    watchdog = Watchdog(stall_step_threshold=15)
    todo = TodoItem(todoId="t1", description="d", doneCriteria="c")
    logs = [_make_log(i, "t1") for i in range(2)]
    scripted_client.queue(
        "diagnose_stall", StallDiagnosis(rootCause="stuck", nextApproach="new plan", shouldRebuildTodo=True)
    )
    assert watchdog.should_intervene(todo, logs) is True


def test_rebuild_todo_only_uses_current_todo_logs(scripted_client):
    watchdog = Watchdog()
    current = TodoItem(todoId="t1", description="current", doneCriteria="c1")
    other = TodoItem(todoId="t2", description="other", doneCriteria="c2")
    logs = [_make_log(0, "t1"), _make_log(1, "t2"), _make_log(2, "t1")]

    scripted_client.queue(
        "decompose_goal_to_todo",
        DecomposeGoalOutput(todos=[TodoItem(todoId="t1b", description="new", doneCriteria="c")], ragContextUsed=[]),
    )
    watchdog.rebuild_todo([current, other], current, logs)

    call = scripted_client.calls[-1]
    assert call["task"] == "decompose_goal_to_todo"
    rag_context = call["payload"]["rag_context"]
    assert len(rag_context) == 2  # t1のログ2件のみ(t2は混入しない)
    assert all("step=0" in s or "step=2" in s for s in rag_context)


def test_rebuild_todo_output_matches_decompose_schema(scripted_client):
    watchdog = Watchdog()
    current = TodoItem(todoId="t1", description="current", doneCriteria="c1")
    expected_todos = [TodoItem(todoId="t1b", description="new", doneCriteria="c")]
    scripted_client.queue(
        "decompose_goal_to_todo", DecomposeGoalOutput(todos=expected_todos, ragContextUsed=[])
    )
    result = watchdog.rebuild_todo([current], current, [])
    assert result == expected_todos
