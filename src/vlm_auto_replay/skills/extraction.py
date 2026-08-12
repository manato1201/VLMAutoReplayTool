"""Phase6: procedureスキル・scriptスキルの自動抽出。"""
from __future__ import annotations

import uuid

from ..actions.skill import Skill
from ..loop.schemas import StepLog
from ..prompts.functions import (
    extract_reusable_subroutine,
    generate_code_from_video_and_procedure,
    merge_duplicate_operations,
)
from ..prompts.schemas import ExtractedSubroutine


def _as_op_seq(logs: list[StepLog]) -> dict:
    return {"actions": [log.actionTaken for log in logs]}


def extract_procedure_skill(candidate_logs: list[list[StepLog]], game_title: str) -> Skill:
    """重複操作の統合(merge_duplicate_operations)からprocedureスキルを合成する。"""
    if not candidate_logs:
        raise ValueError("candidate_logsが空です。")
    merged = merge_duplicate_operations([_as_op_seq(logs) for logs in candidate_logs])
    return Skill(
        skillId=merged.mergedSkillId,
        gameTitle=game_title,
        type="procedure",
        proceduralText=_render_procedure_text(candidate_logs),
        paramSchema=merged.paramSchema,
        createdBy="auto",
        sourceTrace=[logs[0].observationRef for logs in candidate_logs if logs],
    )


def _render_procedure_text(candidate_logs: list[list[StepLog]]) -> str:
    # TODO(Phase6反復): 現状は各ステップのreasoningを連結した簡易版。
    # 将来的にはmerge_duplicate_operationsのparamSchemaと突き合わせて
    # パラメータ化されたテンプレート文へ整形する。
    steps = candidate_logs[0]
    return "\n".join(f"{i + 1}. {log.reasoning}" for i, log in enumerate(steps))


def extract_script_skill(video_ref: str, procedure_text: str, game_title: str) -> Skill:
    """動画+手順書からのPythonコード生成(Voyagerのコード化スキルライブラリを先行事例とする)。"""
    generated = generate_code_from_video_and_procedure(video_ref, procedure_text)
    return Skill(
        skillId=str(uuid.uuid4()),
        gameTitle=game_title,
        type="script",
        scriptCode=generated.scriptCode,
        paramSchema=_infer_param_schema(generated.scriptCode),
        createdBy="auto",
        sourceTrace=generated.sourceTrace or [video_ref],
    )


def _infer_param_schema(script_code: str) -> dict:
    # TODO(Phase6反復): AST解析で `params["x"]` 参照を抽出しスキーマ化する高精度版に置き換える。
    # 現状は「paramSchemaは空でも受け入れる」という最小実装。
    return {}


def extract_reusable_subroutines_from_sessions(session_logs: list[StepLog]) -> list[ExtractedSubroutine]:
    """セッション横断の定期実行を想定した再利用可能サブルーチン抽出。

    呼び出し側はこれをメインループの実行スレッドとは別のバックグラウンド/バッチ
    ジョブから呼び出すこと(メインループの実行時間をブロックしないため)。
    """
    return extract_reusable_subroutine([log.model_dump() for log in session_logs])
