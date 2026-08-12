"""Phase2のStepLogスキーマ。別文書「ProfilingTool設計書」の直接の計装対象。"""
from __future__ import annotations

from pydantic import BaseModel


class StepLog(BaseModel):
    stepIndex: int
    timestamp: str  # ISO8601
    todoId: str
    observationRef: str  # 画面キャプチャの保存先参照(パス/ID)
    reasoning: str  # explain_action_choiceの出力
    actionTaken: dict  # {"type": "skill"|"api", "id": str, "params": dict}
    resultObservationSummary: str  # summarize_screen_changeの出力
