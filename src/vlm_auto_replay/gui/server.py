"""VLMAutoReplayTool 操作用GUIのFastAPIサーバ。

起動: `uvicorn vlm_auto_replay.gui.server:app` または `python -m vlm_auto_replay.gui`
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..prompts.model_client import configure_model_client, get_model_client
from .runtime import DemoModelClient, RuntimeState, render_observation_svg

_STATIC_DIR = Path(__file__).parent / "static"


def _ensure_model_client_configured() -> None:
    """既に実クライアントが configure_model_client() で設定済みならそれを尊重し、
    未設定の場合のみデモ用クライアントを既定で使う。"""
    try:
        get_model_client()
    except RuntimeError:
        configure_model_client(DemoModelClient())


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # モデルクライアントの解決はアプリ起動時(import時ではなく)に行う。import時点で
    # 副作用を発生させると、テストなど「このモジュールをimportするだけ」のコードから
    # 意図せずグローバルなモデルクライアント設定が書き換わってしまうため。
    _ensure_model_client_configured()
    yield


app = FastAPI(title="VLMAutoReplayTool Control Panel", lifespan=_lifespan)
state = RuntimeState()


class DecomposeRequest(BaseModel):
    goal: str


class StartRunRequest(BaseModel):
    todoId: str
    maxSteps: int = Field(default=12, ge=1, le=200)
    stallDemo: bool = False


class AddSkillRequest(BaseModel):
    gameTitle: str = Field(default="MyGame", min_length=1)
    proceduralText: str = Field(min_length=1)


class HidBackendRequest(BaseModel):
    name: str


@app.get("/api/status")
def api_status() -> dict:
    return state.snapshot()


@app.post("/api/todo/decompose")
def api_decompose(req: DecomposeRequest) -> dict:
    if not req.goal.strip():
        raise HTTPException(status_code=400, detail="goalは空にできません。")
    try:
        todos = state.decompose(req.goal)
    except Exception as exc:
        # 実モデルクライアントに差し替えた場合、API障害等でここが例外を投げうる。
        # 未捕捉のまま500 Internal Server Errorになるより、原因をそのままJSONで返す。
        raise HTTPException(status_code=502, detail=f"ゴール分解に失敗しました: {exc}") from exc
    return {"todos": [t.model_dump() for t in todos]}


@app.post("/api/run/start")
def api_start_run(req: StartRunRequest) -> dict:
    try:
        state.start_run(req.todoId, req.maxSteps, stall_demo=req.stallDemo)
    except (KeyError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@app.post("/api/run/stop")
def api_stop_run() -> dict:
    state.stop_run()
    return {"ok": True}


@app.get("/api/observation/{ref}")
def api_observation(ref: str) -> Response:
    observation = state.get_observation(ref)
    if observation is None:
        raise HTTPException(status_code=404, detail=f"観測データが見つかりません: {ref}")
    return Response(content=render_observation_svg(observation), media_type="image/svg+xml")


@app.post("/api/skills")
def api_add_skill(req: AddSkillRequest) -> dict:
    if not req.proceduralText.strip():
        raise HTTPException(status_code=400, detail="proceduralTextは空にできません。")
    skill = state.add_skill(req.gameTitle.strip() or "MyGame", req.proceduralText)
    return {"skill": skill.model_dump()}


@app.delete("/api/skills/{skill_id}")
def api_delete_skill(skill_id: str) -> dict:
    try:
        state.remove_skill(skill_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@app.get("/api/history")
def api_history() -> dict:
    """過去の実行(サーバー再起動をまたいでSQLiteに永続化されたもの)の一覧。"""
    return {"runs": state.list_history()}


@app.get("/api/history/{run_id}")
def api_history_detail(run_id: str) -> dict:
    run = state.get_history_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"実行履歴が見つかりません: {run_id}")
    logs = state.get_history_logs(run_id)
    return {"run": run, "logs": [log.model_dump() for log in logs]}


@app.get("/api/settings")
def api_settings() -> dict:
    return {"hidBackend": state.get_hid_settings()}


@app.post("/api/settings/hid-backend")
def api_set_hid_backend(req: HidBackendRequest) -> dict:
    try:
        state.set_hid_backend(req.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        # 依存パッケージ未インストール/非Windows等、この環境では構築できないことを示す。
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"hidBackend": state.get_hid_settings()}


# APIルートの後にマウントすることで、/api/* を優先させつつそれ以外を静的フロントエンドとして配信する。
app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
