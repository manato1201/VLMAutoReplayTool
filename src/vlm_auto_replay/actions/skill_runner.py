"""Phase3: SkillRunner。procedureスキルはMainLoopを再利用し、scriptスキルはサンドボックス実行。"""
from __future__ import annotations

from ..loop.main_loop import MainLoop
from ..loop.schemas import StepLog
from ..prompts.schemas import TodoItem
from .sandbox import ScriptSandbox
from .skill import Skill


class SkillRunner:
    def __init__(self, main_loop: MainLoop, sandbox: ScriptSandbox):
        self._loop = main_loop
        self._sandbox = sandbox

    def run_procedure_skill(self, skill: Skill, todo: TodoItem, max_steps: int) -> list[StepLog]:
        """procedureスキルは別実行パスを持たない。手順テキストを非強制ガイダンスとして
        MainLoopへ注入し、同じループを再実行する。generate_next_actionの出力が手順から
        逸脱しても止めない(非強制性)。
        """
        if skill.type != "procedure":
            raise ValueError(f"run_procedure_skillはtype=='procedure'のみ対応です: {skill.type}")
        return self._loop.run(todo, max_steps=max_steps, guidance_text=skill.proceduralText)

    def run_script_skill(self, skill: Skill, params: dict) -> None:
        if skill.type != "script":
            raise ValueError(f"run_script_skillはtype=='script'のみ対応です: {skill.type}")
        self._sandbox.execute(skill.scriptCode, params)  # type: ignore[arg-type]
