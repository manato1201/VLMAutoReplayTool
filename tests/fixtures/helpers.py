"""複数のテストファイルで共有するテストダブル群。

以前は test_main_loop.py 内のプライベートクラスを他のテストファイルが
`from test_main_loop import _Foo` の形で直接importしていたが、テストファイル間の
実行順序に依存しない形にするため、共有ダブルはここに集約する。
"""
from __future__ import annotations

from typing import Protocol

from vlm_auto_replay.actions.api_primitives import (
    ApiPrimitives,
    NullKeyboardMouseBackend,
    NullPadBackend,
    ScriptedOcrBackend,
)
from vlm_auto_replay.loop.schemas import StepLog
from vlm_auto_replay.prompts.schemas import NextActionOutput, TodoItem


class InMemoryObservationStore:
    def __init__(self) -> None:
        self.stored: list[bytes] = []

    def store(self, observation: bytes) -> str:
        self.stored.append(observation)
        return f"obs:{len(self.stored) - 1}"


class InMemoryStepLogSink:
    def __init__(self) -> None:
        self.logs: list[StepLog] = []

    def log_step(self, log: StepLog) -> None:
        self.logs.append(log)


class NeverInterveneWatchdog:
    def should_intervene(self, todo: TodoItem, logs: list[StepLog]) -> bool:
        return False


class _Applyable(Protocol):
    def apply(self, action: NextActionOutput) -> None: ...


class FakeGameExecutor:
    """`.apply(action)` を持つゲームダブル(FakeGame/DemoGame等)をActionExecutorへ橋渡しする。"""

    def __init__(self, game: _Applyable):
        self._game = game

    def execute(self, action: NextActionOutput) -> None:
        self._game.apply(action)


def make_null_api() -> ApiPrimitives:
    """HIDバックエンドをすべてNull実装にしたApiPrimitives(何も実機に作用しない)。"""
    return ApiPrimitives(NullPadBackend(), NullKeyboardMouseBackend(), ScriptedOcrBackend())
