"""Phase2: capture→generate_next_action→log_step→execute→状態進行確認→repeat。

固定ディレイでの前進ではなく、summarize_screen_change の結果をもとに状態が
実際に変化したかを確認してからループを進める(Final Phase検証項目と対応)。
"""
from __future__ import annotations

import datetime
from typing import Protocol

from ..prompts.functions import (
    explain_action_choice,
    generate_next_action,
    summarize_screen_change,
)
from ..prompts.schemas import NextActionOutput, TodoItem
from .schemas import StepLog


class ScreenCapture(Protocol):
    """画面キャプチャの抽象インターフェース。

    前例: DevelopmentRAGEnvironment の screen_capture.py (capture_viewport() /
    capture_viewport_clip())。実装自体は別物(ゲーム画面用)だが、
    「画面キャプチャ→VLM」という入力形状は同型。
    """

    def capture(self) -> bytes: ...


class ObservationStore(Protocol):
    """画面キャプチャの永続化先。StepLog.observationRef の参照先を返す。"""

    def store(self, observation: bytes) -> str: ...


class StepLogSink(Protocol):
    """StepLogの永続化先(監査可能性の要件を満たす)。"""

    def log_step(self, log: StepLog) -> None: ...


class ActionExecutor(Protocol):
    """アクション実行の抽象インターフェース。Phase3の実装(ActionDispatcher)を注入する。"""

    def execute(self, action: NextActionOutput) -> None: ...


class Watchdog(Protocol):
    """Phase5 Watchdogの抽象インターフェース(循環import回避のためProtocolで宣言)。"""

    def should_intervene(self, todo: TodoItem, logs: list[StepLog]) -> bool: ...


class TodoDoneChecker(Protocol):
    def __call__(self, todo: TodoItem, observation: bytes) -> bool: ...


class MainLoop:
    def __init__(
        self,
        *,
        capture: ScreenCapture,
        observation_store: ObservationStore,
        step_log_sink: StepLogSink,
        executor: ActionExecutor,
        watchdog: Watchdog,
        todo_done_checker: TodoDoneChecker,
        history_window: int = 5,
    ):
        self._capture = capture
        self._observation_store = observation_store
        self._step_log_sink = step_log_sink
        self._executor = executor
        self._watchdog = watchdog
        self._todo_done_checker = todo_done_checker
        self._history_window = history_window

    def run(self, todo: TodoItem, max_steps: int, guidance_text: str | None = None) -> list[StepLog]:
        logs: list[StepLog] = []
        for step_index in range(max_steps):
            observation = self._capture.capture()
            next_action = generate_next_action(
                todo, observation, self._recent_history(logs), guidance_text=guidance_text
            )
            reasoning = explain_action_choice(next_action, {"todo": todo.model_dump(), "step": step_index})
            if not reasoning.reasoning.strip():
                raise AssertionError(
                    "reasoning が空文字列です。行動前に必ずreasoningをログする要件(監査可能性)に違反します。"
                )

            log = StepLog(
                stepIndex=step_index,
                timestamp=self._now(),
                todoId=todo.todoId,
                observationRef=self._observation_store.store(observation),
                reasoning=reasoning.reasoning,
                actionTaken={
                    "type": next_action.actionType,
                    "id": next_action.actionId,
                    "params": next_action.params,
                },
                resultObservationSummary="",
            )

            before = observation
            self._executor.execute(next_action)
            after = self._capture.capture()
            log.resultObservationSummary = summarize_screen_change(before, after).summary

            logs.append(log)
            self._step_log_sink.log_step(log)

            if self._watchdog.should_intervene(todo, logs):
                break
            if self._todo_done_checker(todo, after):
                break
        return logs

    def _recent_history(self, logs: list[StepLog]) -> list[dict]:
        window = logs[-self._history_window :] if self._history_window > 0 else []
        return [log.model_dump() for log in window]

    @staticmethod
    def _now() -> str:
        return datetime.datetime.now(datetime.UTC).isoformat()
