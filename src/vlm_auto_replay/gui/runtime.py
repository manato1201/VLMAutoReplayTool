"""GUIバックエンドの実行状態。DemoModelClient/DemoGameでコアエンジンを実際に動かす。

重要: ここで使う DemoModelClient は実VLM呼び出しの代替であり、GUIをその場で操作確認
できるようにするための決定的なスタブ。実運用ではこのモジュールを経由せず、
`prompts.model_client.configure_model_client()` に実クライアントを注入すればよい
(GUI側・MainLoop側のコードは一切変更不要 — Phase1の差し替え可能性の要件通り)。
"""
from __future__ import annotations

import re
import threading
import time
import uuid
from typing import Any, ClassVar

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
    def __init__(self, state: RuntimeState):
        self._state = state

    def log_step(self, log: StepLog) -> None:
        with self._state.lock:
            self._state.logs.append(log)


class _StoppableWatchdog:
    """Phase5のWatchdogを包み、GUIからの「停止」要求もshould_interveneに合流させる。"""

    def __init__(self, base: Watchdog):
        self._base = base
        self.stop_requested = False

    @property
    def threshold(self) -> int:
        return self._base.threshold

    def should_intervene(self, todo: TodoItem, logs: list[StepLog]) -> bool:
        if self.stop_requested:
            return True
        return self._base.should_intervene(todo, logs)


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
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.todos: list[TodoItem] = []
        self.logs: list[StepLog] = []
        self.observations: dict[str, bytes] = {}
        self.skill_library: dict[str, Skill] = _default_skill_library()
        self._watchdog = _StoppableWatchdog(Watchdog(stall_step_threshold=8))
        self.status = "idle"  # idle | running | done | stopped
        self.active_todo_id: str | None = None
        self.error: str | None = None
        self._thread: threading.Thread | None = None
        self._current_game: DemoGame | None = None

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

    def start_run(self, todo_id: str, max_steps: int = 12) -> None:
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
            self._watchdog.stop_requested = False

        game = DemoGame(steps_to_win=5, step_delay_sec=0.5)
        with self.lock:
            self._current_game = game

        def _run() -> None:
            loop = MainLoop(
                capture=game,
                observation_store=_StateObservationStore(self),
                step_log_sink=_LiveStepLogSink(self),
                executor=_DemoExecutor(game),
                watchdog=self._watchdog,
                todo_done_checker=lambda t, obs: game.is_done(),
            )
            try:
                loop.run(todo, max_steps=max_steps)
            except Exception as exc:
                with self.lock:
                    self.error = str(exc)
            with self.lock:
                if self.status == "running":
                    self.status = "stopped" if self._watchdog.stop_requested else "done"

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def stop_run(self) -> None:
        self._watchdog.stop_requested = True

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
        return skill

    def remove_skill(self, skill_id: str) -> None:
        with self.lock:
            if skill_id not in self.skill_library:
                raise KeyError(f"未知のskillIdです: {skill_id}")
            del self.skill_library[skill_id]

    def snapshot(self) -> dict:
        with self.lock:
            game = self._current_game
            return {
                "status": self.status,
                "activeTodoId": self.active_todo_id,
                "error": self.error,
                "watchdogThreshold": self._watchdog.threshold,
                "progress": game.progress if game else 0,
                "stepsToWin": game.steps_to_win if game else 0,
                "todos": [t.model_dump() for t in self.todos],
                "logs": [log.model_dump() for log in self.logs],
                "skills": [s.model_dump() for s in self.skill_library.values()],
            }
