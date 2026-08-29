"""VLMAutoReplayTool 操作用GUIのFastAPIサーバ。

起動: `uvicorn vlm_auto_replay.gui.server:app` または `python -m vlm_auto_replay.gui`
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..prompts.model_client import configure_model_client, get_model_client
from .runtime import DemoModelClient, RuntimeState

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
        state.start_run(req.todoId, req.maxSteps)
    except (KeyError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@app.post("/api/run/stop")
def api_stop_run() -> dict:
    state.stop_run()
    return {"ok": True}


# APIルートの後にマウントすることで、/api/* を優先させつつそれ以外を静的フロントエンドとして配信する。
app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
