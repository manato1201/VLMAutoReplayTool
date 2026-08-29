"""SQLiteによる永続化層。

現状はGUIの状態(RuntimeState)がすべてインメモリで、サーバー再起動で消えていた。
スキルライブラリと実行履歴(StepLog)をSQLiteに落とすことで、
「過去の実行ログを見返す」「スキルライブラリが再起動後も残る」を実用にする。

実行履歴はPhase1の`extract_experience`(再利用可能な知見の抽出)の入力データ源にも
なりうるが、そこへの自動投入はこのモジュールのスコープ外(将来の反復ポイント)。
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from ..actions.skill import Skill
from ..loop.schemas import StepLog

DEFAULT_DB_PATH = Path.home() / ".vlm_auto_replay" / "gui.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS skills (
    skill_id TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    todo_id TEXT NOT NULL,
    todo_description TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS run_logs (
    run_id TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (run_id, step_index)
);
"""


def resolve_db_path(db_path: str | Path | None) -> str:
    if db_path is not None:
        return str(db_path)
    DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return str(DEFAULT_DB_PATH)


class SqlitePersistence:
    """スキルライブラリと実行履歴の永続化。呼び出しはすべて内部ロックで直列化する
    (`check_same_thread=False`で複数スレッド — MainLoopのバックグラウンドスレッドと
    FastAPIのリクエストスレッド — から同じ接続を共有するため)。
    """

    def __init__(self, db_path: str | Path | None = None):
        self.path = resolve_db_path(db_path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # --- skills -------------------------------------------------------

    def load_skills(self) -> dict[str, Skill]:
        with self._lock:
            rows = self._conn.execute("SELECT data FROM skills").fetchall()
        skills = (Skill.model_validate_json(row[0]) for row in rows)
        return {skill.skillId: skill for skill in skills}

    def save_skill(self, skill: Skill) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO skills (skill_id, data) VALUES (?, ?)",
                (skill.skillId, skill.model_dump_json()),
            )
            self._conn.commit()

    def delete_skill(self, skill_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM skills WHERE skill_id = ?", (skill_id,))
            self._conn.commit()

    # --- run history ----------------------------------------------------

    def start_run(self, run_id: str, todo_id: str, todo_description: str, started_at: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO runs (run_id, todo_id, todo_description, status, started_at, finished_at) "
                "VALUES (?, ?, ?, 'running', ?, NULL)",
                (run_id, todo_id, todo_description, started_at),
            )
            self._conn.commit()

    def append_log(self, run_id: str, log: StepLog) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO run_logs (run_id, step_index, data) VALUES (?, ?, ?)",
                (run_id, log.stepIndex, log.model_dump_json()),
            )
            self._conn.commit()

    def finish_run(self, run_id: str, status: str, finished_at: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE runs SET status = ?, finished_at = ? WHERE run_id = ?",
                (status, finished_at, run_id),
            )
            self._conn.commit()

    def list_runs(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT run_id, todo_id, todo_description, status, started_at, finished_at "
                "FROM runs ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_run_row_to_dict(row) for row in rows]

    def get_run(self, run_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT run_id, todo_id, todo_description, status, started_at, finished_at FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return _run_row_to_dict(row) if row is not None else None

    def get_run_logs(self, run_id: str) -> list[StepLog]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT data FROM run_logs WHERE run_id = ? ORDER BY step_index", (run_id,)
            ).fetchall()
        return [StepLog.model_validate_json(row[0]) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _run_row_to_dict(row: tuple) -> dict:
    return {
        "runId": row[0],
        "todoId": row[1],
        "todoDescription": row[2],
        "status": row[3],
        "startedAt": row[4],
        "finishedAt": row[5],
    }
