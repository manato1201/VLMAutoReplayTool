"""Final Phase: procedureスキルの非強制性(逸脱の許容)の実証。"""
from __future__ import annotations

from fixtures.fake_game import FakeGame
from fixtures.helpers import (
    FakeGameExecutor,
    InMemoryObservationStore,
    InMemoryStepLogSink,
    NeverInterveneWatchdog,
    make_null_api,
)

from vlm_auto_replay.actions.sandbox import ScriptSandbox
from vlm_auto_replay.actions.skill import Skill
from vlm_auto_replay.actions.skill_runner import SkillRunner
from vlm_auto_replay.loop.main_loop import MainLoop
from vlm_auto_replay.prompts.schemas import ExplainActionOutput, NextActionOutput, ScreenChangeSummary, TodoItem


def test_generate_next_action_output_may_deviate_from_procedure_guidance(scripted_client):
    """generate_next_actionの出力が手順(guidance_text)から逸脱しても実行が妨げられないこと。"""
    game = FakeGame(steps_to_win=1)
    sink = InMemoryStepLogSink()

    loop = MainLoop(
        capture=game,
        observation_store=InMemoryObservationStore(),
        step_log_sink=sink,
        executor=FakeGameExecutor(game),
        watchdog=NeverInterveneWatchdog(),
        todo_done_checker=lambda todo, obs: game.is_done(),
    )
    runner = SkillRunner(main_loop=loop, sandbox=ScriptSandbox(make_null_api()))

    # 手順書は "press B" を指示するが、モデルは全く別の "advance" を選ぶ(逸脱)。
    scripted_client.queue("generate_next_action", NextActionOutput(actionType="api", actionId="advance", params={}))
    scripted_client.queue("explain_action_choice", ExplainActionOutput(reasoning="deviated from guidance on purpose"))
    scripted_client.queue("summarize_screen_change", ScreenChangeSummary(summary="advanced anyway"))

    skill = Skill(
        skillId="s1",
        gameTitle="g",
        type="procedure",
        proceduralText="1. press B repeatedly",
        paramSchema={},
        createdBy="manual",
    )
    todo = TodoItem(todoId="t1", description="win", doneCriteria="progress>=1")
    logs = runner.run_procedure_skill(skill, todo, max_steps=5)

    assert game.is_done()  # 逸脱した行動でも実行は妨げられず、目標は達成された
    assert logs[0].actionTaken["id"] == "advance"  # guidanceの"B"ではなく実際に選ばれた行動が記録される
