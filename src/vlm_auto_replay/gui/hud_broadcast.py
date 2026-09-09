"""Phase8: モニタリングHUD向けWebSocketブロードキャスト。

VLMAutoReplayTool_DESIGN.md Phase8の設計通り、既存スキーマ(StepLog / TodoItem /
StallDiagnosis / ExperienceOutput)をそのままJSONで配信する片方向データフロー。
HUD側からループへの書き込みは一切行わない — このモジュールもpublish専用で、
WebSocket経由でHUDから何かを受け取って状態を変更するAPIは持たない。

RuntimeStateの実行はMainLoopを動かすバックグラウンドスレッド上で行われる一方、
WebSocket配信はFastAPIのasyncioイベントループ上で行う必要があるため、
`asyncio.Queue` + `call_soon_threadsafe` でブリッジする。`publish()`は
どのスレッドから呼んでも安全。

注意: 当初`queue.Queue`をデフォルトのThreadPoolExecutor上でブロッキング待機する
実装だったが、`asyncio.Queue.get()`と違いこの待機はタスクキャンセルで即座に
中断できず、テストのたびにTestClientがexecutorスレッドを作っては残し続け、
プロセス終了時のスレッドjoin待ちでpytestごとハングする不具合があった
(`concurrent.futures.thread._python_exit`が全workerスレッドの終了を待つため)。
"""
from __future__ import annotations

import asyncio
from typing import Any


class HudBroadcaster:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict[str, Any]] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._clients: list[Any] = []  # fastapi.WebSocket(型は循環import回避のためAny)
        self._pump_task: asyncio.Task | None = None

    def publish(self, event: dict[str, Any]) -> None:
        """任意のスレッドから呼び出せる。start()が呼ばれる前(イベントループ未起動)は
        購読者がまだ存在しえないため、静かに無視する。
        """
        if self._loop is None or self._queue is None:
            return
        self._loop.call_soon_threadsafe(self._queue.put_nowait, event)

    async def register(self, websocket: Any) -> None:
        await websocket.accept()
        self._clients.append(websocket)

    def unregister(self, websocket: Any) -> None:
        if websocket in self._clients:
            self._clients.remove(websocket)

    def start(self) -> None:
        """FastAPIのlifespan起動時に一度だけ呼び出し、配信ポンプをバックグラウンドタスクとして開始する。"""
        if self._pump_task is not None:
            return
        self._loop = asyncio.get_event_loop()
        self._queue = asyncio.Queue()
        self._pump_task = asyncio.create_task(self._run_pump())

    def stop(self) -> None:
        if self._pump_task is not None:
            self._pump_task.cancel()
            self._pump_task = None
        self._loop = None
        self._queue = None

    async def _run_pump(self) -> None:
        queue_ = self._queue
        if queue_ is None:  # pragma: no cover - start()の直後にしか呼ばれないため理論上到達しない
            return
        while True:
            event = await queue_.get()
            if not self._clients:
                continue  # 購読者がいなければ破棄する(片方向データフロー、溜め込まない)
            dead: list[Any] = []
            for client in self._clients:
                try:
                    await client.send_json(event)
                except Exception:
                    # 切断済みクライアントの検出のため広く捕捉する。
                    dead.append(client)
            for client in dead:
                self.unregister(client)
