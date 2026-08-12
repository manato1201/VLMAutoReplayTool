"""Phase3: Skillスキーマ。procedure(手順書)/script(Pythonコード)の2種。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, model_validator


class Skill(BaseModel):
    skillId: str
    gameTitle: str
    type: Literal["procedure", "script"]
    proceduralText: str | None = None  # type=="procedure"のみ
    scriptCode: str | None = None  # type=="script"のみ
    paramSchema: dict
    createdBy: Literal["manual", "auto"]
    sourceTrace: list[str] | None = None  # 自動生成時の元ソース参照(動画/ログ)

    @model_validator(mode="after")
    def _check_type_fields(self) -> "Skill":
        if self.type == "procedure" and self.proceduralText is None:
            raise ValueError("type=='procedure' には proceduralText が必須です。")
        if self.type == "script" and self.scriptCode is None:
            raise ValueError("type=='script' には scriptCode が必須です。")
        if self.type == "procedure" and self.scriptCode is not None:
            raise ValueError("type=='procedure' に scriptCode を設定してはいけません。")
        if self.type == "script" and self.proceduralText is not None:
            raise ValueError("type=='script' に proceduralText を設定してはいけません。")
        if self.createdBy == "auto" and not self.sourceTrace:
            raise ValueError("createdBy=='auto' の場合 sourceTrace は非空である必要があります。")
        return self
