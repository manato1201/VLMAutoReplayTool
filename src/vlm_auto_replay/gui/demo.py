"""GUIをその場で操作確認できるようにする決定的なデモ実装群。

`DemoModelClient` は実VLM呼び出しの代替、`DemoGame`/`DemoExecutor` は実HID呼び出しの
代替。実運用ではこのモジュールを経由せず、`prompts.model_client.configure_model_client()`
に実クライアントを注入し、`RuntimeState`にも実ScreenCapture/ActionExecutorを渡せばよい
(GUI側・MainLoop側のコードは一切変更不要 — Phase1の差し替え可能性の要件通り)。
"""
from __future__ import annotations

import time
import uuid
from typing import Any, ClassVar

from ..prompts.schemas import (
    DecomposeGoalOutput,
    ExperienceOutput,
    ExplainActionOutput,
    GeneratedCode,
    MergedOperation,
    NextActionOutput,
    ScreenChangeSummary,
    StallDiagnosis,
    TodoItem,
)


class DemoModelClient:
    """9つのtaskすべてに対して決定的な応答を返すデモ用FoundationModelClient実装。"""

    _ACTION_VERBS: ClassVar[list[str]] = ["周囲を観察する", "目標に向けて前進する", "状況を確認する"]

    def complete(self, *, task: str, payload: dict[str, Any], images: list[bytes] | None, response_model: Any) -> Any:
        handler = getattr(self, f"_{task}", None)
        if handler is None:
            raise NotImplementedError(f"DemoModelClient: 未対応のtaskです: {task}")
        return handler(payload)

    def _decompose_goal_to_todo(self, payload: dict) -> DecomposeGoalOutput:
        goal = payload["goal"]
        todos = [
            TodoItem(
                todoId=uuid.uuid4().hex[:8], description=f"{goal}: 準備", doneCriteria="準備が整った状態が観測できる"
            ),
            TodoItem(
                todoId=uuid.uuid4().hex[:8], description=f"{goal}: 実行", doneCriteria="目標状態への到達が観測できる"
            ),
            TodoItem(todoId=uuid.uuid4().hex[:8], description=f"{goal}: 確認", doneCriteria="達成が確認できる"),
        ]
        return DecomposeGoalOutput(todos=todos, ragContextUsed=payload.get("rag_context", []))

    def _generate_next_action(self, payload: dict) -> NextActionOutput:
        step = len(payload.get("history", []))
        return NextActionOutput(actionType="api", actionId="advance", params={"step": step})

    def _explain_action_choice(self, payload: dict) -> ExplainActionOutput:
        step = payload["context"].get("step", 0)
        todo_desc = payload["context"]["todo"]["description"]
        verb = self._ACTION_VERBS[step % len(self._ACTION_VERBS)]
        return ExplainActionOutput(reasoning=f"「{todo_desc}」達成のため、{verb}(ステップ{step})")

    def _summarize_screen_change(self, payload: dict) -> ScreenChangeSummary:
        return ScreenChangeSummary(summary="画面上の進捗インジケータが1段階進行した")

    def _extract_experience(self, payload: dict) -> ExperienceOutput:
        return ExperienceOutput(
            title="デモ実行の経験", summary="デモ実行が正常に進行した", problem="なし", betterWay="なし"
        )

    def _merge_duplicate_operations(self, payload: dict) -> MergedOperation:
        return MergedOperation(mergedSkillId=uuid.uuid4().hex[:8], paramSchema={})

    def _generate_code_from_video_and_procedure(self, payload: dict) -> GeneratedCode:
        return GeneratedCode(scriptCode="pass\n", sourceTrace=[payload.get("video_ref", "")])

    def _extract_reusable_subroutine(self, payload: dict) -> list:
        return []

    def _diagnose_stall(self, payload: dict) -> StallDiagnosis:
        return StallDiagnosis(rootCause="停滞なし(デモ)", nextApproach="続行", shouldRebuildTodo=False)


class DemoGame:
    """決定的なデモ用のゲーム代替。実運用ではScreenCapture/ActionExecutorを実HIDに差し替える。"""

    def __init__(self, steps_to_win: int = 5, step_delay_sec: float = 0.5):
        self._progress = 0
        self._steps_to_win = steps_to_win
        # 見た目のペース調整のみ。ループの進行判定はresultObservationSummaryに基づき、
        # このディレイに依存しない(Phase2の要件通り)。
        self._step_delay_sec = step_delay_sec

    @property
    def progress(self) -> int:
        return self._progress

    @property
    def steps_to_win(self) -> int:
        return self._steps_to_win

    def capture(self) -> bytes:
        return f"progress={self._progress}/{self._steps_to_win}".encode()

    def apply(self, action: NextActionOutput) -> None:
        if action.actionType == "api" and action.actionId == "advance":
            self._progress += 1
        time.sleep(self._step_delay_sec)

    def is_done(self) -> bool:
        return self._progress >= self._steps_to_win


class DemoExecutor:
    def __init__(self, game: DemoGame):
        self._game = game

    def execute(self, action: NextActionOutput) -> None:
        self._game.apply(action)
