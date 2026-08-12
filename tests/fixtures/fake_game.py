"""Final Phase: 決定的フェイクゲーム。入力に対する応答が固定・再現可能。"""
from __future__ import annotations

from vlm_auto_replay.prompts.schemas import NextActionOutput


class FakeGame:
    def __init__(self, steps_to_win: int = 3):
        self._progress = 0
        self._steps_to_win = steps_to_win
        self.capture_calls = 0

    def capture(self) -> bytes:
        self.capture_calls += 1
        return f"progress={self._progress}".encode()

    def apply(self, action: NextActionOutput) -> None:
        if action.actionType == "api" and action.actionId == "advance":
            self._progress += 1

    def is_done(self) -> bool:
        return self._progress >= self._steps_to_win

    @property
    def progress(self) -> int:
        return self._progress
