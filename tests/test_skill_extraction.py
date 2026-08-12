"""Phase6/Final Phase: スキル自動抽出+受け入れゲートの検証。"""
from __future__ import annotations

from vlm_auto_replay.loop.schemas import StepLog
from vlm_auto_replay.prompts.schemas import MergedOperation, TodoItem
from vlm_auto_replay.skills.extraction import extract_procedure_skill
from vlm_auto_replay.skills.gate import accept_skill


def _log(step: int, action_id: str) -> StepLog:
    return StepLog(
        stepIndex=step,
        timestamp="2026-08-12T00:00:00Z",
        todoId="t1",
        observationRef=f"obs:{step}",
        reasoning="repeat pattern",
        actionTaken={"type": "api", "id": action_id, "params": {}},
        resultObservationSummary="advanced",
    )


def test_three_step_repeat_fixture_produces_exactly_one_merged_skill(scripted_client):
    """明らかな3ステップ反復フィクスチャから正確に1つのマージ済みSkillが生成される。"""
    candidate_logs = [
        [_log(0, "advance"), _log(1, "advance"), _log(2, "advance")],
        [_log(0, "advance"), _log(1, "advance"), _log(2, "advance")],
        [_log(0, "advance"), _log(1, "advance"), _log(2, "advance")],
    ]
    scripted_client.queue(
        "merge_duplicate_operations", MergedOperation(mergedSkillId="merged-1", paramSchema={"n": "int"})
    )

    skill = extract_procedure_skill(candidate_logs, game_title="FakeGameTitle")

    assert skill.skillId == "merged-1"
    assert skill.type == "procedure"
    assert skill.createdBy == "auto"
    assert skill.sourceTrace and len(skill.sourceTrace) == 3
    # merge_duplicate_operationsは1回だけ呼び出され、1つのSkillに統合されること
    calls = [c for c in scripted_client.calls if c["task"] == "merge_duplicate_operations"]
    assert len(calls) == 1
    assert len(calls[0]["payload"]["candidate_ops"]) == 3


def test_accept_skill_requires_all_holdout_scenarios_to_pass():
    from vlm_auto_replay.actions.skill import Skill

    candidate = Skill(
        skillId="s1", gameTitle="g", type="script", scriptCode="pass", paramSchema={}, createdBy="auto",
        sourceTrace=["log:1"],
    )
    scenarios = [
        TodoItem(todoId="t1", description="d1", doneCriteria="c1"),
        TodoItem(todoId="t2", description="d2", doneCriteria="c2"),
    ]

    assert accept_skill(candidate, scenarios, replay_test=lambda c, s: True) is True
    assert accept_skill(candidate, scenarios, replay_test=lambda c, s: s.todoId == "t1") is False
