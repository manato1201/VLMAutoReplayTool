"""Phase1: 9関数の型付き入出力とモデル差し替え可能性の検証。"""
from __future__ import annotations

import pytest

from vlm_auto_replay.prompts import functions as fn
from vlm_auto_replay.prompts.model_client import get_model_client, reset_model_client
from vlm_auto_replay.prompts.schemas import (
    DecomposeGoalOutput,
    ExperienceOutput,
    ExplainActionOutput,
    ExtractedSubroutine,
    GeneratedCode,
    MergedOperation,
    NextActionOutput,
    ScreenChangeSummary,
    StallDiagnosis,
    TodoItem,
)


def test_get_model_client_raises_when_unconfigured():
    reset_model_client()
    with pytest.raises(RuntimeError):
        get_model_client()


def test_decompose_goal_to_todo_returns_typed_output(scripted_client):
    expected = DecomposeGoalOutput(
        todos=[TodoItem(todoId="t1", description="d", doneCriteria="c")], ragContextUsed=["doc:1"]
    )
    scripted_client.queue("decompose_goal_to_todo", expected)
    result = fn.decompose_goal_to_todo("goal", ["ctx"])
    assert result == expected
    assert scripted_client.calls[0]["payload"]["rag_context"] == ["ctx"]


def test_generate_next_action_never_receives_rag_context(scripted_client):
    """generate_next_actionのペイロードにrag関連キーが含まれないことを確認する(Phase4境界)。"""
    expected = NextActionOutput(actionType="api", actionId="advance", params={})
    scripted_client.queue("generate_next_action", expected)
    todo = TodoItem(todoId="t1", description="d", doneCriteria="c")
    result = fn.generate_next_action(todo, b"obs", [])
    assert result == expected
    payload = scripted_client.calls[0]["payload"]
    assert "rag_context" not in payload
    assert "rag_client" not in payload
    assert scripted_client.calls[0]["images_count"] == 1


def test_explain_action_choice(scripted_client):
    expected = ExplainActionOutput(reasoning="because X")
    scripted_client.queue("explain_action_choice", expected)
    action = NextActionOutput(actionType="api", actionId="advance", params={})
    assert fn.explain_action_choice(action, {}) == expected


def test_extract_experience(scripted_client):
    expected = ExperienceOutput(title="t", summary="s", problem="p", betterWay="b")
    scripted_client.queue("extract_experience", expected)
    assert fn.extract_experience([{"a": 1}]) == expected


def test_summarize_screen_change_passes_two_images(scripted_client):
    expected = ScreenChangeSummary(summary="s")
    scripted_client.queue("summarize_screen_change", expected)
    assert fn.summarize_screen_change(b"a", b"b") == expected
    assert scripted_client.calls[0]["images_count"] == 2


def test_merge_duplicate_operations(scripted_client):
    expected = MergedOperation(mergedSkillId="s1", paramSchema={})
    scripted_client.queue("merge_duplicate_operations", expected)
    assert fn.merge_duplicate_operations([{"op": "a"}]) == expected


def test_generate_code_from_video_and_procedure(scripted_client):
    expected = GeneratedCode(scriptCode="pass", sourceTrace=["frame:1"])
    scripted_client.queue("generate_code_from_video_and_procedure", expected)
    assert fn.generate_code_from_video_and_procedure("video:1", "text") == expected


def test_extract_reusable_subroutine_returns_list(scripted_client):
    expected = [ExtractedSubroutine(subroutineId="s1", occurrenceCount=3, candidateActions=["a"])]
    scripted_client.queue("extract_reusable_subroutine", expected)
    assert fn.extract_reusable_subroutine([{"log": 1}]) == expected


def test_diagnose_stall(scripted_client):
    expected = StallDiagnosis(rootCause="r", nextApproach="n", shouldRebuildTodo=False)
    scripted_client.queue("diagnose_stall", expected)
    todo = TodoItem(todoId="t1", description="d", doneCriteria="c")
    assert fn.diagnose_stall(todo, []) == expected


def test_model_swap_does_not_require_caller_changes(scripted_client):
    """モデル実装を差し替えても呼び出し側コードは変更不要であることの確認。"""
    from vlm_auto_replay.prompts.model_client import configure_model_client

    class AlternateClient:
        def complete(self, *, task, payload, images, response_model):
            return NextActionOutput(actionType="api", actionId="alt", params={})

    configure_model_client(AlternateClient())
    todo = TodoItem(todoId="t1", description="d", doneCriteria="c")
    result = fn.generate_next_action(todo, b"obs", [])
    assert result.actionId == "alt"
