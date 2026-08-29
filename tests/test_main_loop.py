"""Phase2 + Final Phase: 決定的フェイクゲームでのMainLoop完走テスト。"""
from __future__ import annotations

from fixtures.fake_game import FakeGame
from fixtures.helpers import (
    FakeGameExecutor,
    InMemoryObservationStore,
    InMemoryStepLogSink,
    NeverInterveneWatchdog,
)

from vlm_auto_replay.loop.main_loop import MainLoop
from vlm_auto_replay.prompts.schemas import (
    ExplainActionOutput,
    NextActionOutput,
    ScreenChangeSummary,
    TodoItem,
)


def _build_loop(game: FakeGame, sink: InMemoryStepLogSink, watchdog=None) -> MainLoop:
    return MainLoop(
        capture=game,
        observation_store=InMemoryObservationStore(),
        step_log_sink=sink,
        executor=FakeGameExecutor(game),
        watchdog=watchdog or NeverInterveneWatchdog(),
        todo_done_checker=lambda todo, obs: game.is_done(),
    )


def _queue_advance_step(scripted_client, reasoning: str = "advance because goal requires progress"):
    scripted_client.queue("generate_next_action", NextActionOutput(actionType="api", actionId="advance", params={}))
    scripted_client.queue("explain_action_choice", ExplainActionOutput(reasoning=reasoning))
    scripted_client.queue("summarize_screen_change", ScreenChangeSummary(summary="progress advanced by one"))


def test_main_loop_completes_deterministic_fake_game(scripted_client):
    game = FakeGame(steps_to_win=3)
    sink = InMemoryStepLogSink()
    loop = _build_loop(game, sink)
    for _ in range(3):
        _queue_advance_step(scripted_client)

    todo = TodoItem(todoId="t1", description="win the fake game", doneCriteria="progress>=3")
    logs = loop.run(todo, max_steps=10)

    assert len(logs) == 3
    assert game.is_done()
    for i, log in enumerate(logs):
        assert log.stepIndex == i
        assert log.todoId == "t1"
        assert log.reasoning
        assert log.resultObservationSummary
        assert log.observationRef


def test_main_loop_progresses_only_via_state_change_not_fixed_delay(scripted_client):
    """resultObservationSummaryによる状態変化確認後にのみループが進むこと(固定ディレイに依存しない)。"""
    import inspect

    from vlm_auto_replay.loop import main_loop as main_loop_module

    source = inspect.getsource(main_loop_module)
    assert "sleep" not in source

    game = FakeGame(steps_to_win=1)
    sink = InMemoryStepLogSink()
    loop = _build_loop(game, sink)
    _queue_advance_step(scripted_client)
    todo = TodoItem(todoId="t1", description="win", doneCriteria="progress>=1")
    logs = loop.run(todo, max_steps=10)
    assert len(logs) == 1
    assert logs[0].resultObservationSummary == "progress advanced by one"


def test_step_index_monotonic_and_no_gaps_with_watchdog_intervention(scripted_client):
    game = FakeGame(steps_to_win=100)  # 絶対に完走しない
    sink = InMemoryStepLogSink()

    class _InterveneAfterTwo:
        def should_intervene(self, todo, logs) -> bool:
            return len(logs) >= 2

    loop = _build_loop(game, sink, watchdog=_InterveneAfterTwo())
    for _ in range(3):
        _queue_advance_step(scripted_client)

    todo = TodoItem(todoId="t1", description="win", doneCriteria="progress>=100")
    logs = loop.run(todo, max_steps=10)

    assert [log.stepIndex for log in logs] == [0, 1]


def test_reasoning_must_not_be_empty(scripted_client):
    game = FakeGame(steps_to_win=1)
    sink = InMemoryStepLogSink()
    loop = _build_loop(game, sink)
    scripted_client.queue("generate_next_action", NextActionOutput(actionType="api", actionId="advance", params={}))
    scripted_client.queue("explain_action_choice", ExplainActionOutput(reasoning="   "))
    todo = TodoItem(todoId="t1", description="win", doneCriteria="progress>=1")
    import pytest

    with pytest.raises(AssertionError):
        loop.run(todo, max_steps=1)
