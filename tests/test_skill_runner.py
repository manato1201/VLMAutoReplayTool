"""Phase3: SkillRunner・サンドボックス・Skillスキーマの検証。"""
from __future__ import annotations

import pytest
from fixtures.fake_game import FakeGame
from vlm_auto_replay.actions.api_primitives import (
    ApiPrimitives,
    NullKeyboardMouseBackend,
    NullPadBackend,
    ScriptedOcrBackend,
)
from vlm_auto_replay.actions.dispatcher import ActionDispatcher
from vlm_auto_replay.actions.sandbox import SandboxViolationError, ScriptSandbox
from vlm_auto_replay.actions.skill import Skill
from vlm_auto_replay.actions.skill_runner import SkillRunner
from vlm_auto_replay.loop.main_loop import MainLoop
from vlm_auto_replay.loop.schemas import StepLog
from vlm_auto_replay.prompts.schemas import ExplainActionOutput, NextActionOutput, ScreenChangeSummary, TodoItem

from test_main_loop import _InMemoryObservationStore, _InMemoryStepLogSink, _NeverInterveneWatchdog


def _make_api() -> ApiPrimitives:
    return ApiPrimitives(NullPadBackend(), NullKeyboardMouseBackend(), ScriptedOcrBackend())


def test_skill_type_procedure_requires_procedural_text():
    with pytest.raises(ValueError):
        Skill(
            skillId="s1", gameTitle="g", type="procedure", paramSchema={}, createdBy="manual"
        )


def test_skill_type_script_rejects_procedural_text():
    with pytest.raises(ValueError):
        Skill(
            skillId="s1",
            gameTitle="g",
            type="script",
            scriptCode="pass",
            proceduralText="should not be set",
            paramSchema={},
            createdBy="manual",
        )


def test_skill_auto_requires_source_trace():
    with pytest.raises(ValueError):
        Skill(skillId="s1", gameTitle="g", type="script", scriptCode="pass", paramSchema={}, createdBy="auto")


def test_run_procedure_skill_reuses_main_loop_not_separate_path(scripted_client):
    """procedureスキル実行がSkillRunner内で独立ループを持たず、MainLoop.runを再利用していること。"""
    game = FakeGame(steps_to_win=1)
    sink = _InMemoryStepLogSink()

    class _Executor:
        def execute(self, action: NextActionOutput) -> None:
            game.apply(action)

    loop = MainLoop(
        capture=game,
        observation_store=_InMemoryObservationStore(),
        step_log_sink=sink,
        executor=_Executor(),
        watchdog=_NeverInterveneWatchdog(),
        todo_done_checker=lambda todo, obs: game.is_done(),
    )
    runner = SkillRunner(main_loop=loop, sandbox=ScriptSandbox(_make_api()))

    scripted_client.queue("generate_next_action", NextActionOutput(actionType="api", actionId="advance", params={}))
    scripted_client.queue("explain_action_choice", ExplainActionOutput(reasoning="follow guidance"))
    scripted_client.queue("summarize_screen_change", ScreenChangeSummary(summary="advanced"))

    skill = Skill(
        skillId="s1",
        gameTitle="g",
        type="procedure",
        proceduralText="1. advance",
        paramSchema={},
        createdBy="manual",
    )
    todo = TodoItem(todoId="t1", description="win", doneCriteria="progress>=1")
    logs = runner.run_procedure_skill(skill, todo, max_steps=5)

    assert isinstance(logs, list) and isinstance(logs[0], StepLog)
    assert scripted_client.calls[0]["payload"]["guidance_text"] == "1. advance"
    assert game.is_done()


def test_run_script_skill_executes_and_calls_api_primitive():
    api = _make_api()
    sandbox = ScriptSandbox(api)
    runner = SkillRunner(main_loop=None, sandbox=sandbox)  # type: ignore[arg-type]
    skill = Skill(
        skillId="s1",
        gameTitle="g",
        type="script",
        scriptCode="api.pad_input(params['button'], 50)",
        paramSchema={"button": "str"},
        createdBy="auto",
        sourceTrace=["video:1"],
    )
    runner.run_script_skill(skill, {"button": "A"})
    pad_backend = api._pad  # type: ignore[attr-defined]
    assert pad_backend.calls == [("A", 50)]


def test_sandbox_blocks_filesystem_and_network_access():
    api = _make_api()
    sandbox = ScriptSandbox(api)
    with pytest.raises(SandboxViolationError):
        sandbox.execute("import os\nos.system('echo hi')", {})
    with pytest.raises(SandboxViolationError):
        sandbox.execute("import socket", {})


def test_action_dispatcher_routes_api_and_script_skill():
    api = _make_api()
    sandbox = ScriptSandbox(api)
    skill = Skill(
        skillId="s1",
        gameTitle="g",
        type="script",
        scriptCode="api.key_input(params['k'], 10)",
        paramSchema={},
        createdBy="auto",
        sourceTrace=["log:1"],
    )
    runner = SkillRunner(main_loop=None, sandbox=sandbox)  # type: ignore[arg-type]
    dispatcher = ActionDispatcher(api=api, skill_library={"s1": skill}, skill_runner=runner)

    dispatcher.execute(NextActionOutput(actionType="api", actionId="mouse_move", params={"dx": 1, "dy": 2}))
    assert api._km.move_calls == [(1, 2)]  # type: ignore[attr-defined]

    dispatcher.execute(NextActionOutput(actionType="skill", actionId="s1", params={"k": "space"}))
    assert api._km.key_calls == [("space", 10)]  # type: ignore[attr-defined]


def test_action_dispatcher_rejects_single_step_procedure_skill():
    api = _make_api()
    sandbox = ScriptSandbox(api)
    skill = Skill(
        skillId="s1", gameTitle="g", type="procedure", proceduralText="do it", paramSchema={}, createdBy="manual"
    )
    runner = SkillRunner(main_loop=None, sandbox=sandbox)  # type: ignore[arg-type]
    dispatcher = ActionDispatcher(api=api, skill_library={"s1": skill}, skill_runner=runner)
    with pytest.raises(ValueError):
        dispatcher.execute(NextActionOutput(actionType="skill", actionId="s1", params={}))
