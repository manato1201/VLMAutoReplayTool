"""GUIバックエンドの実行状態(RuntimeState)。DemoModelClient/DemoGameでコアエンジンを
実際に動かす(実装は`demo.py`、観測画像レンダリングは`observation_render.py`に分離)。
"""
from __future__ import annotations

import datetime
import threading
import uuid
from pathlib import Path

from ..actions.api_primitives import (
    ApiPrimitives,
    KeyboardMouseBackend,
    NullKeyboardMouseBackend,
    NullPadBackend,
    PadBackend,
    ScriptedOcrBackend,
    SendInputBackend,
    ViGEmPadBackend,
)
from ..actions.skill import Skill
from ..loop.main_loop import MainLoop
from ..loop.schemas import StepLog
from ..loop.watchdog import Watchdog
from ..prompts.functions import decompose_goal_to_todo, extract_experience
from ..prompts.schemas import StallDiagnosis, TodoItem
from .demo import DemoExecutor, DemoGame
from .hud_broadcast import HudBroadcaster
from .persistence import SqlitePersistence


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


HID_BACKEND_LABELS: dict[str, str] = {
    "none": "未接続(Null、何も実機に作用しない)",
    "sendinput": "SendInput(実キーボード・マウス操作、ctypes経由)",
    "vigem": "ViGEm(仮想Xboxパッド、要vgamepad+ViGEmBus)",
}


def _build_hid_backends(name: str) -> tuple[PadBackend, KeyboardMouseBackend]:
    """選んだバックエンド名からPad/KeyboardMouseの実装ペアを構築する。

    実際に構築するだけで、まだGUIのデモ実行フロー(DemoGame/DemoExecutor)には
    接続しない — DemoGame側はHIDを一切経由しない決定的なスタブなので、ここで
    選んだバックエンドは主に「この環境でPhase3の実HID実装が構築できるか」を
    その場で確認する目的と、実運用でScreenCapture/ActionExecutorを実キャプチャ・
    実HIDに差し替える際の設定の起点になる。
    """
    if name == "none":
        return NullPadBackend(), NullKeyboardMouseBackend()
    if name == "sendinput":
        return NullPadBackend(), SendInputBackend()
    if name == "vigem":
        return ViGEmPadBackend(), NullKeyboardMouseBackend()
    raise ValueError(f"未知のHIDバックエンドです: {name}")


class _StateObservationStore:
    """観測バイト列をRuntimeStateへ保存し、`/api/observation/{ref}`から参照可能にする。"""

    def __init__(self, state: RuntimeState):
        self._state = state

    def store(self, observation: bytes) -> str:
        ref = f"obs-{uuid.uuid4().hex[:8]}"
        with self._state.lock:
            self._state.observations[ref] = observation
        return ref


class _LiveStepLogSink:
    def __init__(self, state: RuntimeState, run_id: str):
        self._state = state
        self._run_id = run_id

    def log_step(self, log: StepLog) -> None:
        with self._state.lock:
            self._state.logs.append(log)
        self._state.db.append_log(self._run_id, log)
        self._state.hud.publish({"type": "step_log", "log": log.model_dump()})


class _StoppableWatchdog:
    """Phase5のWatchdogを包み、GUIからの「停止」要求もshould_interveneに合流させる。

    `last_intervention_reason`でshould_intervene()がTrueを返した理由(ユーザーによる
    停止か、Phase5の二重条件による自動介入か)を区別できるようにし、GUIが
    「Watchdogが実際に介入した」ことを検知してTODO再構築のデモ表示に使えるようにする。
    """

    def __init__(self, base: Watchdog):
        self._base = base
        self.stop_requested = False
        self.last_intervention_reason: str | None = None  # "user_stop" | "watchdog" | None

    @property
    def threshold(self) -> int:
        return self._base.threshold

    @property
    def last_diagnosis(self) -> StallDiagnosis | None:
        return self._base.last_diagnosis

    def reset(self) -> None:
        self.stop_requested = False
        self.last_intervention_reason = None

    def should_intervene(self, todo: TodoItem, logs: list[StepLog]) -> bool:
        if self.stop_requested:
            self.last_intervention_reason = "user_stop"
            return True
        if self._base.should_intervene(todo, logs):
            self.last_intervention_reason = "watchdog"
            return True
        return False

    def rebuild_todo(
        self, current_todos: list[TodoItem], current_todo: TodoItem, logs: list[StepLog]
    ) -> list[TodoItem]:
        return self._base.rebuild_todo(current_todos, current_todo, logs)


def _default_skill_library() -> dict[str, Skill]:
    return {
        "demo-skill-1": Skill(
            skillId="demo-skill-1",
            gameTitle="DemoGame",
            type="procedure",
            proceduralText="1. 周囲を観察する\n2. 目標に向けて前進する\n3. 達成を確認する",
            paramSchema={},
            createdBy="manual",
        )
    }


class RuntimeState:
    def __init__(self, db_path: str | Path | None = None, hud: HudBroadcaster | None = None) -> None:
        self.lock = threading.Lock()
        self.db = SqlitePersistence(db_path)
        self.hud = hud or HudBroadcaster()
        self.todos: list[TodoItem] = []
        self.logs: list[StepLog] = []
        self.observations: dict[str, bytes] = {}
        self.completed_todo_ids: set[str] = set()
        self.skill_library: dict[str, Skill] = self._load_or_seed_skill_library()
        self._watchdog = _StoppableWatchdog(Watchdog(stall_step_threshold=8))
        self.status = "idle"  # idle | running | done | stopped | error | intervened
        self.active_todo_id: str | None = None
        self.error: str | None = None
        self.last_intervention: dict | None = None
        self._thread: threading.Thread | None = None
        self._current_game: DemoGame | None = None
        self.hid_backend_name = "none"
        self.hid_backend_error: str | None = None
        self.api_primitives = ApiPrimitives(*_build_hid_backends("none"), ScriptedOcrBackend())

    def _load_or_seed_skill_library(self) -> dict[str, Skill]:
        loaded = self.db.load_skills()
        if loaded:
            return loaded
        # 初回起動時はデモスキルを1件だけ永続化しておく(2回目以降の起動でも残る)。
        seed = _default_skill_library()
        for skill in seed.values():
            self.db.save_skill(skill)
        return seed

    def _publish_todos(self) -> None:
        with self.lock:
            todos = list(self.todos)
            completed = list(self.completed_todo_ids)
        self.hud.publish(
            {"type": "todos", "todos": [t.model_dump() for t in todos], "completedTodoIds": completed}
        )

    def decompose(self, goal: str) -> list[TodoItem]:
        result = decompose_goal_to_todo(goal, rag_context=[])
        with self.lock:
            self.todos = result.todos
            self.logs = []
            self.observations = {}
            self.completed_todo_ids = set()
            self.status = "idle"
            self.active_todo_id = None
            self.error = None
            self._current_game = None
        # self.logsをリセットしたのと同様に、HUD(hud.js)が保持しているStepLog/
        # ドットチャートの蓄積もクリアさせる。これが無いと、REST版ダッシュボード
        # (index.html)の実行ログは新しいゴールの分解と同時に空になるのに対し、
        # HUD側だけ前のゴールのStepLogが残り続けるという不整合が生じる。
        self.hud.publish({"type": "logs_reset"})
        self._publish_todos()
        return result.todos

    def start_run(self, todo_id: str, max_steps: int = 12, stall_demo: bool = False) -> None:
        """stall_demo=Trueの場合、意図的に(現実的な範囲で)完了しないDemoGameを使う。
        これによりWatchdogの閾値到達による自動介入とTODO再構築を確実に発生させ、
        その様子をGUI上で確認できるようにする(通常実行では閾値未満で完了することが多く、
        Watchdogが介入する場面が自然には見えないため)。
        """
        with self.lock:
            if self.status == "running":
                raise RuntimeError("既に実行中です。停止してから再度開始してください。")
            todo = next((t for t in self.todos if t.todoId == todo_id), None)
            if todo is None:
                raise KeyError(f"未知のtodoIdです: {todo_id}")
            self.logs = []
            self.observations = {}
            self.status = "running"
            self.active_todo_id = todo_id
            self.error = None
            self._watchdog.reset()

        run_id = f"run-{uuid.uuid4().hex[:10]}"
        self.db.start_run(run_id, todo.todoId, todo.description, started_at=_now_iso())
        # HUD(hud.js)はstepIndexを一意なキーとしてドットチャート/ログフィードを描画するため、
        # 新しいRunが始まったことを明示的に伝え、前のRunのStepLogの蓄積をクリアさせる。
        # これが無いと、2回目以降の実行でstepIndexが0から再スタートし、
        # 前のRunのドット/ログと混ざって表示されてしまう。
        self.hud.publish({"type": "run_started", "runId": run_id, "todoId": todo_id})

        steps_to_win = 999 if stall_demo else 5
        game = DemoGame(steps_to_win=steps_to_win, step_delay_sec=0.3 if stall_demo else 0.5)
        with self.lock:
            self._current_game = game

        def _run() -> None:
            loop = MainLoop(
                capture=game,
                observation_store=_StateObservationStore(self),
                step_log_sink=_LiveStepLogSink(self, run_id),
                executor=DemoExecutor(game),
                watchdog=self._watchdog,
                todo_done_checker=lambda t, obs: game.is_done(),
            )
            logs: list[StepLog] = []
            try:
                logs = loop.run(todo, max_steps=max_steps)
            except Exception as exc:
                with self.lock:
                    self.error = str(exc)
            with self.lock:
                if self.status == "running":
                    if self.error is not None:
                        self.status = "error"
                    elif self._watchdog.last_intervention_reason == "watchdog":
                        self.status = "intervened"
                    else:
                        self.status = "stopped" if self._watchdog.stop_requested else "done"
                final_status = self.status
            if final_status == "intervened":
                self._apply_watchdog_rebuild(todo, logs)
            elif final_status == "done":
                with self.lock:
                    self.completed_todo_ids.add(todo.todoId)
                self._publish_todos()
                self._publish_experience(logs)
            self.db.finish_run(run_id, final_status, finished_at=_now_iso())

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def _apply_watchdog_rebuild(self, stalled_todo: TodoItem, logs: list[StepLog]) -> None:
        """Watchdogが自動介入した後、Phase5のrebuild_todoでTODOを再構築し、
        停滞したTODOだけを新しいTODO群に差し替える(他の未完了TODOは残す)。
        """
        with self.lock:
            current_todos = list(self.todos)
        new_todos = self._watchdog.rebuild_todo(current_todos, stalled_todo, logs)
        with self.lock:
            self.todos = [t for t in self.todos if t.todoId != stalled_todo.todoId] + new_todos
            self.last_intervention = {
                "todoId": stalled_todo.todoId,
                "todoDescription": stalled_todo.description,
                "stepCount": len(logs),
                "newTodoIds": [t.todoId for t in new_todos],
                "occurredAt": _now_iso(),
            }
            intervention = dict(self.last_intervention)
        # last_diagnosisは「直近のdiagnose_stall呼び出し結果」であり、閾値到達(強制発動)経路では
        # 更新されない(=前回呼び出し時点の使い回し)。今回の介入が実際にdiagnose_stallの
        # shouldRebuildTodo==Trueによるものだった場合に限り、その内容を信頼できる理由として扱う。
        # そうでなければ古い(無関係な)診断結果をHUDに誤表示してしまう。
        diagnosis = self._watchdog.last_diagnosis
        diagnosis_is_trigger = diagnosis is not None and diagnosis.shouldRebuildTodo
        self.hud.publish(
            {
                "type": "watchdog_intervened",
                "stepIndex": logs[-1].stepIndex if logs else None,
                "message": (
                    f"「{intervention['todoDescription']}」が{intervention['stepCount']}ステップ継続したため、"
                    f"{len(intervention['newTodoIds'])}件の新しいTODOへ再構築しました。"
                ),
                "diagnosis": diagnosis.model_dump() if diagnosis_is_trigger else None,
                "intervention": intervention,
            }
        )
        self._publish_todos()

    def _publish_experience(self, logs: list[StepLog]) -> None:
        """完走したRunのStepLogから再利用可能な知見を抽出し(Phase1 extract_experience)、
        HUDのスタックトースト向けに配信する。ループ本体(MainLoop)には一切影響しない
        — 完走後の後処理としてGUI/HUD層でのみ呼び出す。
        """
        if not logs:
            return
        experience = extract_experience([log.model_dump() for log in logs])
        self.hud.publish({"type": "experience", "experience": experience.model_dump()})

    def stop_run(self) -> None:
        self._watchdog.stop_requested = True

    def set_hid_backend(self, name: str) -> None:
        """HIDバックエンドを切り替える。構築に失敗した場合(未インストール/非Windows等)は
        例外を送出し、現在のバックエンドは変更しない。
        """
        if name not in HID_BACKEND_LABELS:
            raise ValueError(f"未知のHIDバックエンドです: {name}")
        try:
            pad, keyboard_mouse = _build_hid_backends(name)
        except RuntimeError as exc:
            with self.lock:
                self.hid_backend_error = str(exc)
            raise
        with self.lock:
            self.hid_backend_name = name
            self.hid_backend_error = None
            self.api_primitives = ApiPrimitives(pad, keyboard_mouse, ScriptedOcrBackend())

    def get_hid_settings(self) -> dict:
        with self.lock:
            return {
                "current": self.hid_backend_name,
                "error": self.hid_backend_error,
                "options": [{"name": name, "label": label} for name, label in HID_BACKEND_LABELS.items()],
            }

    def get_observation(self, ref: str) -> bytes | None:
        with self.lock:
            return self.observations.get(ref)

    def add_skill(self, game_title: str, procedural_text: str) -> Skill:
        skill = Skill(
            skillId=f"skill-{uuid.uuid4().hex[:8]}",
            gameTitle=game_title,
            type="procedure",
            proceduralText=procedural_text,
            paramSchema={},
            createdBy="manual",
        )
        with self.lock:
            self.skill_library[skill.skillId] = skill
        self.db.save_skill(skill)
        self._publish_skills()
        return skill

    def remove_skill(self, skill_id: str) -> None:
        with self.lock:
            if skill_id not in self.skill_library:
                raise KeyError(f"未知のskillIdです: {skill_id}")
            del self.skill_library[skill_id]
        self.db.delete_skill(skill_id)
        self._publish_skills()

    def _publish_skills(self) -> None:
        with self.lock:
            skills = list(self.skill_library.values())
        self.hud.publish({"type": "skills", "skills": [s.model_dump() for s in skills]})

    def list_history(self, limit: int = 20) -> list[dict]:
        return self.db.list_runs(limit)

    def get_history_run(self, run_id: str) -> dict | None:
        return self.db.get_run(run_id)

    def get_history_logs(self, run_id: str) -> list[StepLog]:
        return self.db.get_run_logs(run_id)

    def snapshot(self) -> dict:
        with self.lock:
            game = self._current_game
            return {
                "status": self.status,
                "activeTodoId": self.active_todo_id,
                "error": self.error,
                "lastIntervention": self.last_intervention,
                "watchdogThreshold": self._watchdog.threshold,
                "progress": game.progress if game else 0,
                "stepsToWin": game.steps_to_win if game else 0,
                "completedTodoIds": list(self.completed_todo_ids),
                "todos": [t.model_dump() for t in self.todos],
                "logs": [log.model_dump() for log in self.logs],
                "skills": [s.model_dump() for s in self.skill_library.values()],
            }
