"""Phase5: スタック検知+Watchdog+TODO再構築(頑健性)。

一時的な失敗は許容する(即座に再構築しない)。再構築トリガーは以下の**いずれか**が
成立した場合のみ発火する(二重条件、ユーザー原文通り):
1. diagnose_stall が「回復不能」と判断した場合(StallDiagnosis.shouldRebuildTodo == True)
2. 同一TODOがNステップ以上継続した場合(強制発動)
"""

from __future__ import annotations

from ..prompts.functions import decompose_goal_to_todo, diagnose_stall
from ..prompts.schemas import TodoItem
from .schemas import StepLog


class Watchdog:
    def __init__(self, stall_step_threshold: int = 15):
        self._threshold = stall_step_threshold

    def should_intervene(self, todo: TodoItem, logs: list[StepLog]) -> bool:
        same_todo_logs = [log for log in logs if log.todoId == todo.todoId]

        # 条件2: 同一TODOがNステップ以上継続(強制発動)
        if len(same_todo_logs) >= self._threshold:
            return True

        # 条件1: diagnose_stallが回復不能と判断
        diagnosis = diagnose_stall(todo, self._as_attempt_log(same_todo_logs))
        return diagnosis.shouldRebuildTodo

    def rebuild_todo(
        self, current_todos: list[TodoItem], current_todo: TodoItem, logs: list[StepLog]
    ) -> list[TodoItem]:
        """再構築の入力は「現在のTODOリスト + そのTODOの過去試行ログ」のみ。

        セッション全体ログは渡さない(スコープの厳密化)。RAGクライアントは一切
        使用しない(rag_context には現在TODOの過去ログを文字列化したものだけを渡す)。
        """
        attempt_log = [log for log in logs if log.todoId == current_todo.todoId]
        return decompose_goal_to_todo(
            goal=self._reconstruct_goal(current_todos, current_todo),
            rag_context=[self._serialize_log(log) for log in attempt_log],
        ).todos

    @staticmethod
    def _as_attempt_log(logs: list[StepLog]) -> list[dict]:
        return [log.model_dump() for log in logs]

    @staticmethod
    def _serialize_log(log: StepLog) -> str:
        return f"step={log.stepIndex} action={log.actionTaken} result={log.resultObservationSummary}"

    @staticmethod
    def _reconstruct_goal(current_todos: list[TodoItem], current_todo: TodoItem) -> str:
        others = ", ".join(
            t.description for t in current_todos if t.todoId != current_todo.todoId
        )
        base = f"次のTODOで停滞したため再計画する: {current_todo.description}"
        return f"{base}(他の未完了TODO: {others})" if others else base
