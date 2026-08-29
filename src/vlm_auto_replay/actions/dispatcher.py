"""Phase2/3の接続部。MainLoop.ActionExecutor を実装し、1ステップ分のNextActionOutputを
API primitiveまたはscriptスキルへ振り分ける。

procedureスキルは1ステップ単位では実行できない(SkillRunner.run_procedure_skillが
MainLoop.runそのものを再帰的に起動する形になるため)。ここではapi呼び出しと
scriptスキル呼び出しのみをディスパッチする。
"""
from __future__ import annotations

from ..prompts.schemas import NextActionOutput
from .api_primitives import ApiPrimitives
from .skill import Skill
from .skill_runner import SkillRunner

# ApiPrimitivesの公開primitiveのみを明示的に許可する。getattrで任意属性を無条件に
# 呼び出すと、actionIdの値次第でApiPrimitives上の想定外の属性(将来追加される
# private helperや内部状態)まで呼び出せてしまうため、ホワイトリストで絞る。
_ALLOWED_API_PRIMITIVES = frozenset({"pad_input", "key_input", "mouse_move", "ocr", "movement"})


class ActionDispatcher:
    def __init__(self, api: ApiPrimitives, skill_library: dict[str, Skill], skill_runner: SkillRunner):
        self._api = api
        self._skills = skill_library
        self._runner = skill_runner

    def execute(self, action: NextActionOutput) -> None:
        if action.actionType == "api":
            self._dispatch_api(action.actionId, action.params)
        elif action.actionType == "skill":
            self._dispatch_skill(action.actionId, action.params)
        else:
            raise ValueError(f"未知のactionTypeです: {action.actionType}")

    def _dispatch_api(self, action_id: str, params: dict) -> None:
        if action_id not in _ALLOWED_API_PRIMITIVES:
            raise KeyError(f"未知のAPI primitiveです: {action_id}")
        primitive = getattr(self._api, action_id)
        primitive(**params)

    def _dispatch_skill(self, action_id: str, params: dict) -> None:
        skill = self._skills.get(action_id)
        if skill is None:
            raise KeyError(f"未知のskillIdです: {action_id}")
        if skill.type != "script":
            raise ValueError(
                f"procedureスキル({action_id})は1ステップ実行できません。"
                " SkillRunner.run_procedure_skill から独立したTODOとして起動してください。"
            )
        self._runner.run_script_skill(skill, params)
