"""Phase1のPydanticスキーマ。設計書 VLMAutoReplayTool_DESIGN.md Phase1 の入出力定義そのまま。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class TodoItem(BaseModel):
    todoId: str
    description: str
    doneCriteria: str  # 完了判定に使う観測可能な条件


class DecomposeGoalOutput(BaseModel):
    todos: list[TodoItem]
    ragContextUsed: list[str]  # 参照した知識ソースのID(監査用)


class ExplainActionOutput(BaseModel):
    reasoning: str  # 簡潔な説明(1〜3文)


class NextActionOutput(BaseModel):
    actionType: Literal["skill", "api"]
    actionId: str
    params: dict


class ExperienceOutput(BaseModel):
    title: str
    summary: str
    problem: str
    betterWay: str


class ScreenChangeSummary(BaseModel):
    summary: str  # 観測できる変化のみ、1〜2文


class MergedOperation(BaseModel):
    mergedSkillId: str
    paramSchema: dict  # 差分をParameter化したスキーマ


class GeneratedCode(BaseModel):
    scriptCode: str
    sourceTrace: list[str]  # 元にした動画フレーム/手順書箇所の参照


class ExtractedSubroutine(BaseModel):
    subroutineId: str
    occurrenceCount: int
    candidateActions: list[str]


class StallDiagnosis(BaseModel):
    rootCause: str
    nextApproach: str
    shouldRebuildTodo: bool
