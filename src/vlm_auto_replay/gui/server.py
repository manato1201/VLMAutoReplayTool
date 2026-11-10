"""VLMAutoReplayTool 操作用GUIのFastAPIサーバ。

起動: `uvicorn vlm_auto_replay.gui.server:app` または `python -m vlm_auto_replay.gui`
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..prompts.model_client import configure_model_client, get_model_client
from .demo import DemoModelClient
from . import auth, auth_routes
from .observation_render import render_observation_svg
from .runtime import RuntimeState
from .security import install_request_guard, websocket_allowed

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
    auth.ensure_auth_configured()  # VLM_AUTH=required でユーザーが居なければ、ここで起動を止める
    _ensure_model_client_configured()
    # Phase8: HUD配信ポンプもアプリのasyncioイベントループ上で起動する(importやRuntimeState
    # 構築の時点ではまだイベントループが無いため、ここで初めて開始できる)。
    state.hud.start()
    yield
    state.hud.stop()


app = FastAPI(title="VLMAutoReplayTool Control Panel", lifespan=_lifespan)
# 認証の無いローカル GUI(実際に入力操作を起動できる)を、別サイトのページ(CSRF)や DNS rebinding から守る
install_request_guard(app)
# 認証と役割(gui/auth.py)。既定は off(従来どおり)。VLM_AUTH=required で、/api/* は viewer 以上を要求する。
# 実行の開始・停止は operator、スキルの変更と入力バックエンド(HID)の変更は admin。/api/auth/* は認証なし。
app.include_router(auth_routes.router)
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


@app.get("/api/status", dependencies=[auth.viewer])
def api_status() -> dict:
    return state.snapshot()


@app.post("/api/todo/decompose", dependencies=[auth.operator])
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


@app.post("/api/run/start", dependencies=[auth.operator])
def api_start_run(req: StartRunRequest) -> dict:
    try:
        state.start_run(req.todoId, req.maxSteps, stall_demo=req.stallDemo)
    except (KeyError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@app.post("/api/run/stop", dependencies=[auth.operator])
def api_stop_run() -> dict:
    state.stop_run()
    return {"ok": True}


@app.get("/api/observation/{ref}", dependencies=[auth.viewer])
def api_observation(ref: str) -> Response:
    observation = state.get_observation(ref)
    if observation is None:
        raise HTTPException(status_code=404, detail=f"観測データが見つかりません: {ref}")
    return Response(content=render_observation_svg(observation), media_type="image/svg+xml")


@app.post("/api/skills", dependencies=[auth.admin])
def api_add_skill(req: AddSkillRequest) -> dict:
    if not req.proceduralText.strip():
        raise HTTPException(status_code=400, detail="proceduralTextは空にできません。")
    skill = state.add_skill(req.gameTitle.strip() or "MyGame", req.proceduralText)
    return {"skill": skill.model_dump()}


@app.delete("/api/skills/{skill_id}", dependencies=[auth.admin])
def api_delete_skill(skill_id: str) -> dict:
    try:
        state.remove_skill(skill_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@app.get("/api/history", dependencies=[auth.viewer])
def api_history() -> dict:
    """過去の実行(サーバー再起動をまたいでSQLiteに永続化されたもの)の一覧。"""
    return {"runs": state.list_history()}


@app.get("/api/history/{run_id}", dependencies=[auth.viewer])
def api_history_detail(run_id: str) -> dict:
    run = state.get_history_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"実行履歴が見つかりません: {run_id}")
    logs = state.get_history_logs(run_id)
    return {"run": run, "logs": [log.model_dump() for log in logs]}


@app.get("/api/settings", dependencies=[auth.viewer])
def api_settings() -> dict:
    return {"hidBackend": state.get_hid_settings()}


@app.post("/api/settings/hid-backend", dependencies=[auth.admin])
def api_set_hid_backend(req: HidBackendRequest) -> dict:
    try:
        state.set_hid_backend(req.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        # 依存パッケージ未インストール/非Windows等、この環境では構築できないことを示す。
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"hidBackend": state.get_hid_settings()}


@app.websocket("/ws/hud")
async def ws_hud(websocket: WebSocket) -> None:
    """Phase8: モニタリングHUD向けの片方向配信。クライアントからの受信内容は無視する
    (HUDからループへの書き込みは行わない設計)。

    `finally`でunregisterすることで、切断がWebSocketDisconnect以外の例外
    (プロトコルエラー等)として現れた場合でも購読者リストに残留させない。
    """
    # ブラウザは WebSocket に同一オリジン制限をかけない。別サイトのページが観測を購読できないよう、
    # ハンドシェイクの Host / Origin を検査する(ミドルウェアは WebSocket を通らない)。
    if not websocket_allowed(websocket.headers):
        await websocket.close(code=1008)
        return
    # 認証(VLM_AUTH=required のとき viewer 以上)。ハンドシェイクのヘッダーかクッキーで確認する。
    if auth.principal_for_websocket(websocket, "viewer") is None:
        await websocket.close(code=1008)
        return
    await state.hud.register(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        state.hud.unregister(websocket)


# APIルートの後にマウントすることで、/api/* を優先させつつそれ以外を静的フロントエンドとして配信する。
app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
