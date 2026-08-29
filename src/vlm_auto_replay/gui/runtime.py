"""GUIバックエンドの実行状態。DemoModelClient/DemoGameでコアエンジンを実際に動かす。

重要: ここで使う DemoModelClient は実VLM呼び出しの代替であり、GUIをその場で操作確認
できるようにするための決定的なスタブ。実運用ではこのモジュールを経由せず、
`prompts.model_client.configure_model_client()` に実クライアントを注入すればよい
(GUI側・MainLoop側のコードは一切変更不要 — Phase1の差し替え可能性の要件通り)。
"""
from __future__ import annotations

import datetime
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, ClassVar

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
from ..prompts.functions import decompose_goal_to_todo
from ..prompts.schemas import (
    DecomposeGoalOutput,
    ExperienceOutput,
    ExplainActionOutput,
    GeneratedCode,
    MergedOperation,
    NextActionOutput,
    ScreenChangeSummary,
    StallDiagnosis,
    TodoItem,
)
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

    実際に構築するだけで、まだGUIのデモ実行フロー(DemoGame/_DemoExecutor)には
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


class DemoModelClient:
    """9つのtaskすべてに対して決定的な応答を返すデモ用FoundationModelClient実装。"""

    _ACTION_VERBS: ClassVar[list[str]] = ["周囲を観察する", "目標に向けて前進する", "状況を確認する"]

    def complete(self, *, task: str, payload: dict[str, Any], images: list[bytes] | None, response_model: Any) -> Any:
        handler = getattr(self, f"_{task}", None)
        if handler is None:
            raise NotImplementedError(f"DemoModelClient: 未対応のtaskです: {task}")
        return handler(payload)

    def _decompose_goal_to_todo(self, payload: dict) -> DecomposeGoalOutput:
        goal = payload["goal"]
        todos = [
            TodoItem(
                todoId=uuid.uuid4().hex[:8], description=f"{goal}: 準備", doneCriteria="準備が整った状態が観測できる"
            ),
            TodoItem(
                todoId=uuid.uuid4().hex[:8], description=f"{goal}: 実行", doneCriteria="目標状態への到達が観測できる"
            ),
            TodoItem(todoId=uuid.uuid4().hex[:8], description=f"{goal}: 確認", doneCriteria="達成が確認できる"),
        ]
        return DecomposeGoalOutput(todos=todos, ragContextUsed=payload.get("rag_context", []))

    def _generate_next_action(self, payload: dict) -> NextActionOutput:
        step = len(payload.get("history", []))
        return NextActionOutput(actionType="api", actionId="advance", params={"step": step})

    def _explain_action_choice(self, payload: dict) -> ExplainActionOutput:
        step = payload["context"].get("step", 0)
        todo_desc = payload["context"]["todo"]["description"]
        verb = self._ACTION_VERBS[step % len(self._ACTION_VERBS)]
        return ExplainActionOutput(reasoning=f"「{todo_desc}」達成のため、{verb}(ステップ{step})")

    def _summarize_screen_change(self, payload: dict) -> ScreenChangeSummary:
        return ScreenChangeSummary(summary="画面上の進捗インジケータが1段階進行した")

    def _extract_experience(self, payload: dict) -> ExperienceOutput:
        return ExperienceOutput(
            title="デモ実行の経験", summary="デモ実行が正常に進行した", problem="なし", betterWay="なし"
        )

    def _merge_duplicate_operations(self, payload: dict) -> MergedOperation:
        return MergedOperation(mergedSkillId=uuid.uuid4().hex[:8], paramSchema={})

    def _generate_code_from_video_and_procedure(self, payload: dict) -> GeneratedCode:
        return GeneratedCode(scriptCode="pass\n", sourceTrace=[payload.get("video_ref", "")])

    def _extract_reusable_subroutine(self, payload: dict) -> list:
        return []

    def _diagnose_stall(self, payload: dict) -> StallDiagnosis:
        return StallDiagnosis(rootCause="停滞なし(デモ)", nextApproach="続行", shouldRebuildTodo=False)


class DemoGame:
    """決定的なデモ用のゲーム代替。実運用ではScreenCapture/ActionExecutorを実HIDに差し替える。"""

    def __init__(self, steps_to_win: int = 5, step_delay_sec: float = 0.5):
        self._progress = 0
        self._steps_to_win = steps_to_win
        # 見た目のペース調整のみ。ループの進行判定はresultObservationSummaryに基づき、
        # このディレイに依存しない(Phase2の要件通り)。
        self._step_delay_sec = step_delay_sec

    @property
    def progress(self) -> int:
        return self._progress

    @property
    def steps_to_win(self) -> int:
        return self._steps_to_win

    def capture(self) -> bytes:
        return f"progress={self._progress}/{self._steps_to_win}".encode()

    def apply(self, action: NextActionOutput) -> None:
        if action.actionType == "api" and action.actionId == "advance":
            self._progress += 1
        time.sleep(self._step_delay_sec)

    def is_done(self) -> bool:
        return self._progress >= self._steps_to_win


_DEMO_OBSERVATION_RE = re.compile(r"progress=(\d+)/(\d+)")


def _render_progress_svg(progress: int, steps_to_win: int) -> bytes:
    """DemoGameの観測(`progress=X/Y`)をStepLogのサムネイル用SVGへ変換する。

    実運用でScreenCaptureを実キャプチャに差し替えた場合、observationRefは実際の
    スクリーンショットバイト列/URLを指すため、この合成描画は経由しない
    (GET /api/observation/{ref} がそのままバイト列を返すだけでよくなる)。
    """
    width, height = 240, 135
    ratio = (progress / steps_to_win) if steps_to_win else 0.0
    bar_width = round(200 * min(max(ratio, 0.0), 1.0))
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        f'<rect width="{width}" height="{height}" fill="#150f23"/>'
        f'<text x="16" y="32" fill="#ffffff" font-family="Rubik, sans-serif" font-size="14" '
        f'font-weight="600">DEMO SCREEN</text>'
        f'<rect x="16" y="60" width="200" height="16" rx="8" fill="#362d59"/>'
        f'<rect x="16" y="60" width="{bar_width}" height="16" rx="8" fill="#c2ef4e"/>'
        f'<text x="16" y="100" fill="#bdb8c0" font-family="Rubik, sans-serif" font-size="12">'
        f"progress {progress} / {steps_to_win}</text>"
        f"</svg>"
    ).encode()


def _render_placeholder_svg(observation: bytes) -> bytes:
    text = observation.decode(errors="ignore")[:40]
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="135" viewBox="0 0 240 135">'
        '<rect width="240" height="135" fill="#150f23"/>'
        '<text x="16" y="32" fill="#ffffff" font-family="Rubik, sans-serif" font-size="14" '
        'font-weight="600">OBSERVATION</text>'
        f'<text x="16" y="70" fill="#bdb8c0" font-family="Monaco, monospace" font-size="11">{text}</text>'
        "</svg>"
    ).encode()


def render_observation_svg(observation: bytes) -> bytes:
    """観測バイト列(デモ形式)をStepLogサムネイル用のSVGにレンダリングする。"""
    match = _DEMO_OBSERVATION_RE.fullmatch(observation.decode(errors="ignore"))
    if match is None:
        return _render_placeholder_svg(observation)
    return _render_progress_svg(int(match.group(1)), int(match.group(2)))


class _StateObservationStore:
    """観測バイト列をRuntimeStateへ保存し、`/api/observation/{ref}`から参照可能にする。"""

    def __init__(self, state: RuntimeState):
        self._state = state

    def store(self, observation: bytes) -> str:
        ref = f"obs-{uuid.uuid4().hex[:8]}"
        with self._state.lock:
            self._state.observations[ref] = observation
        return ref


class _DemoExecutor:
    def __init__(self, game: DemoGame):
        self._game = game

    def execute(self, action: NextActionOutput) -> None:
        self._game.apply(action)


class _LiveStepLogSink:
    def __init__(self, state: RuntimeState, run_id: str):
        self._state = state
        self._run_id = run_id

    def log_step(self, log: StepLog) -> None:
        with self._state.lock:
            self._state.logs.append(log)
        self._state.db.append_log(self._run_id, log)


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
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.lock = threading.Lock()
        self.db = SqlitePersistence(db_path)
        self.todos: list[TodoItem] = []
        self.logs: list[StepLog] = []
        self.observations: dict[str, bytes] = {}
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

    def decompose(self, goal: str) -> list[TodoItem]:
        result = decompose_goal_to_todo(goal, rag_context=[])
        with self.lock:
            self.todos = result.todos
            self.logs = []
            self.observations = {}
            self.status = "idle"
            self.active_todo_id = None
            self.error = None
            self._current_game = None
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

        steps_to_win = 999 if stall_demo else 5
        game = DemoGame(steps_to_win=steps_to_win, step_delay_sec=0.3 if stall_demo else 0.5)
        with self.lock:
            self._current_game = game

        def _run() -> None:
            loop = MainLoop(
                capture=game,
                observation_store=_StateObservationStore(self),
                step_log_sink=_LiveStepLogSink(self, run_id),
                executor=_DemoExecutor(game),
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
        return skill

    def remove_skill(self, skill_id: str) -> None:
        with self.lock:
            if skill_id not in self.skill_library:
                raise KeyError(f"未知のskillIdです: {skill_id}")
            del self.skill_library[skill_id]
        self.db.delete_skill(skill_id)

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
                "todos": [t.model_dump() for t in self.todos],
                "logs": [log.model_dump() for log in self.logs],
                "skills": [s.model_dump() for s in self.skill_library.values()],
            }
