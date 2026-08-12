"""Phase7: ScreenState。状態遷移グラフのノードとして定義する。"""
from __future__ import annotations

from pydantic import BaseModel


class ScreenState(BaseModel):
    stateId: str
    referenceImageRef: str
    ocrLandmarks: list[str]  # final confirmationで照合するテキストランドマーク
    knownTransitions: list[str]  # 遷移可能な次stateIdのリスト(状態遷移グラフ)


class UnknownTransitionError(Exception):
    """knownTransitionsに存在しない遷移が発生した場合に送出する。"""


def validate_transition(previous_state: ScreenState, next_state: ScreenState) -> None:
    """状態遷移グラフの整合性チェック。previous_state.knownTransitionsに
    next_state.stateIdが含まれない場合はUnknownTransitionErrorを送出する。
    """
    if next_state.stateId not in previous_state.knownTransitions:
        raise UnknownTransitionError(
            f"'{previous_state.stateId}' から '{next_state.stateId}' への遷移は"
            " knownTransitionsに定義されていません。"
        )
