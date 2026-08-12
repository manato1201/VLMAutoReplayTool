"""Phase1: 9つのプロンプト意図に1対1対応する型付き関数群。

ユーザー原文の9意図(VLMAutoReplayTool_DESIGN.md Phase0参照):
1. decompose_goal_to_todo               達成に必要な作業をTODOリストに分解
2. explain_action_choice                なぜそのアクションにしたのか説明
3. generate_next_action                 Goal達成のための次アクション生成
4. extract_experience                   再利用可能な高価値知見の抽出
5. summarize_screen_change              画面上の観測可能な変化の要約
6. merge_duplicate_operations           重複操作の統合(差分のParameter化)
7. generate_code_from_video_and_procedure  動画+手順書からのPythonコード生成
8. extract_reusable_subroutine          再利用可能サブルーチンの自動切り出し
9. diagnose_stall                       停滞原因の分析と次アプローチの特定

重要な境界(Phase4参照): このモジュールは `knowledge.rag_client` を一切importしない。
generate_next_action はRAGコンテキストを一切受け取らない。RAGはTODO生成の"前段"
(knowledge/experience.py の decompose_goal_with_rag)でのみ使用され、ここに渡ってくる
時点ではすでに `rag_context: list[str]` という解決済みの文字列列になっている。
"""
from __future__ import annotations

from .model_client import get_model_client
from .schemas import (
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


def decompose_goal_to_todo(goal: str, rag_context: list[str]) -> DecomposeGoalOutput:
    """「達成するために必要な作業をTODOリストに分解してください」"""
    return get_model_client().complete(
        task="decompose_goal_to_todo",
        payload={"goal": goal, "rag_context": rag_context},
        images=None,
        response_model=DecomposeGoalOutput,
    )


def explain_action_choice(action: NextActionOutput, context: dict) -> ExplainActionOutput:
    """「なぜそのアクションにしたのか、簡潔に説明して」"""
    return get_model_client().complete(
        task="explain_action_choice",
        payload={"action": action.model_dump(), "context": context},
        images=None,
        response_model=ExplainActionOutput,
    )


def generate_next_action(
    todo: TodoItem,
    screen_obs: bytes,
    history: list[dict],
    guidance_text: str | None = None,
) -> NextActionOutput:
    """「Goalを達成するために次のアクションを生成してください」

    RAGクライアントへの参照を一切持たない(import自体をしない)。procedureスキル実行時に
    Phase3のSkillRunnerから渡される guidance_text は非強制の参考情報としてのみ添付され、
    従わない出力(逸脱)を妨げない。
    """
    return get_model_client().complete(
        task="generate_next_action",
        payload={
            "todo": todo.model_dump(),
            "history": history,
            "guidance_text": guidance_text,
        },
        images=[screen_obs],
        response_model=NextActionOutput,
    )


def extract_experience(play_log: list[dict]) -> ExperienceOutput:
    """「再利用可能な高価値の知見(落とし穴、操作ルール、前提条件)を抽出して」"""
    return get_model_client().complete(
        task="extract_experience",
        payload={"play_log": play_log},
        images=None,
        response_model=ExperienceOutput,
    )


def summarize_screen_change(before: bytes, after: bytes) -> ScreenChangeSummary:
    """「画面上に観測できる変化だけを1〜2文で要約して」"""
    return get_model_client().complete(
        task="summarize_screen_change",
        payload={},
        images=[before, after],
        response_model=ScreenChangeSummary,
    )


def merge_duplicate_operations(candidate_ops: list[dict]) -> MergedOperation:
    """「重複操作をまとめて、差分をParameterにして1つに統合して」"""
    return get_model_client().complete(
        task="merge_duplicate_operations",
        payload={"candidate_ops": candidate_ops},
        images=None,
        response_model=MergedOperation,
    )


def generate_code_from_video_and_procedure(video_ref: str, procedure_text: str) -> GeneratedCode:
    """「動画と手順書から良い感じにPythonコード作って」"""
    return get_model_client().complete(
        task="generate_code_from_video_and_procedure",
        payload={"video_ref": video_ref, "procedure_text": procedure_text},
        images=None,
        response_model=GeneratedCode,
    )


def extract_reusable_subroutine(session_logs: list[dict]) -> list[ExtractedSubroutine]:
    """「再利用可能サブルーチンとして自動的に切り出してください」"""
    return get_model_client().complete(
        task="extract_reusable_subroutine",
        payload={"session_logs": session_logs},
        images=None,
        response_model=list[ExtractedSubroutine],
    )


def diagnose_stall(current_todo: TodoItem, todo_attempt_log: list[dict]) -> StallDiagnosis:
    """「処理が停滞している原因を、分析し、次に試すべきアプローチを特定して」"""
    return get_model_client().complete(
        task="diagnose_stall",
        payload={"current_todo": current_todo.model_dump(), "todo_attempt_log": todo_attempt_log},
        images=None,
        response_model=StallDiagnosis,
    )
