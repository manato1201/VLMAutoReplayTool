"""gui/security.py

認証を持たないローカルの操作 GUI(127.0.0.1)を、ブラウザ経由の攻撃から守る最小限の検査
(ToolOrchestrationHub の hub/security.py と同じ考え方。2026-11-10 に移植)。

この GUI は ``POST /api/run/start`` で**実際にキー入力・クリックを起動する**。ブラウザで別のサイトを
開いているだけで、そのページのスクリプトが localhost のこの API を叩けると、意図しない操作が走りうる。

- **Host ヘッダの許可リスト**: 既定はループバックのみ(DNS rebinding 対策)。別のホスト名で開くときは
  環境変数 ``VLM_ALLOWED_HOSTS``(カンマ区切り。ポートは無視)に足す。
- **変更操作(POST/PUT/PATCH/DELETE)の Origin** が、このリクエスト自身の Host と一致すること。
  Origin を付けないクライアント(curl・テスト・他ツールからの呼び出し)は通す。``Origin: null`` は弾く。
- **WebSocket(/ws/hud)**: ブラウザは WebSocket に同一オリジン制限をかけないので、ハンドシェイクの
  Host と Origin を同じ基準で検査する(``websocket_allowed``)。ミドルウェアは WebSocket を通らない。

これは**認証ではない**。複数人で使う場合は、この上に認証と役割(実行できる人)が要る。
"""
from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

ALLOWED_HOSTS_ENV = "VLM_ALLOWED_HOSTS"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def host_only(host_header: str) -> str:
    """Host ヘッダからポートを除いたホスト名を小文字で返す(``[::1]:8765`` → ``::1``)。"""
    host = host_header.strip().lower()
    if host.startswith("["):
        end = host.find("]")
        return host[1:end] if end != -1 else host
    if host.count(":") == 1:
        host = host.split(":", 1)[0]
    return host


def resolve_allowed_hosts(extra: Iterable[str] | None = None) -> frozenset[str]:
    """ループバック + 環境変数 VLM_ALLOWED_HOSTS + 引数の許可ホスト。"""
    allowed = set(LOOPBACK_HOSTS)
    for item in [*os.environ.get(ALLOWED_HOSTS_ENV, "").split(","), *(extra or [])]:
        item = item.strip()
        if item:
            allowed.add(host_only(item))
    return frozenset(allowed)


def is_same_origin(origin: str, host_header: str) -> bool:
    """Origin のホストとポートが Host ヘッダと一致するか(スキームは見ない)。``null`` は常に不一致。"""
    if origin.strip().lower() == "null":
        return False
    return urlsplit(origin).netloc.lower() == host_header.strip().lower()


def websocket_allowed(headers: Mapping[str, str]) -> bool:
    """WebSocket のハンドシェイクを通してよいか。Host が許可リストにあり、Origin があれば同一オリジン。"""
    host_header = headers.get("host", "")
    if host_only(host_header) not in resolve_allowed_hosts():
        return False
    origin = headers.get("origin")
    return origin is None or is_same_origin(origin, host_header)


def install_request_guard(app: FastAPI) -> None:
    """全 HTTP リクエストの Host と、変更操作の Origin を検査するミドルウェアを入れる。"""

    @app.middleware("http")
    async def request_guard(request: Request, call_next):
        host_header = request.headers.get("host", "")
        if host_only(host_header) not in resolve_allowed_hosts():
            return JSONResponse(
                status_code=403,
                content={
                    "detail": (
                        f"この Host では受け付けません: {host_header!r}。"
                        f"127.0.0.1 / localhost 以外のホスト名で開く場合は、環境変数 {ALLOWED_HOSTS_ENV} に追加してください"
                    )
                },
            )
        if request.method in UNSAFE_METHODS:
            origin = request.headers.get("origin")
            if origin is not None and not is_same_origin(origin, host_header):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "他のサイトからの変更操作は受け付けません(Origin が一致しません)"},
                )
        return await call_next(request)
