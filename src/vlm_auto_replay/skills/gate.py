"""Phase6: 受け入れゲート。新規スキルはホールドアウトテストを通過してから登録する。"""
from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple

from ..actions.skill import Skill
from ..prompts.schemas import TodoItem


class ReplayResult(NamedTuple):
    scenario: TodoItem
    succeeded: bool


ReplayTestRunner = Callable[[Skill, TodoItem], bool]


def accept_skill(
    candidate: Skill, holdout_scenarios: list[TodoItem], replay_test: ReplayTestRunner
) -> bool:
    """holdout_scenariosすべてに対するreplay_testが成功した場合のみTrueを返す。

    replay_testの実体(実際のreplay/liveテスト実行)は呼び出し側が注入する
    (実ゲーム/実VLMに依存するため、このモジュールでは実装しない)。
    """
    if not holdout_scenarios:
        raise ValueError("holdout_scenariosが空です。受け入れゲートを素通りさせないこと。")
    results = [ReplayResult(scenario, replay_test(candidate, scenario)) for scenario in holdout_scenarios]
    return all(r.succeeded for r in results)
